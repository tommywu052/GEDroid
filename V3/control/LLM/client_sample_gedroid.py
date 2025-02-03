# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import os
import sys
import sounddevice as sd
import wave
import time
import threading
import re
import config

from openai import AzureOpenAI
import numpy as np
import soundfile as sf
from azure.core.credentials import AzureKeyCredential
import azure.cognitiveservices.speech as speechsdk
from dotenv import load_dotenv
from scipy.signal import resample

import scipy.io.wavfile as wav
import speech_recognition as sr
import pyttsx3
import openai
import json
import os
import sys
import webrtcvad

import whisper
import pyaudio
import torch

import paho.mqtt.client as mqtt

from rtclient import (
    InputAudioTranscription,
    RTAudioContent,
    RTClient,
    RTFunctionCallItem,
    RTInputAudioItem,
    RTMessageItem,
    RTResponse,
    ServerVAD,
    NoTurnDetection
)
start_time = None

from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Retrieve variables from environment
speech_key = os.getenv("SPEECH_KEY")
speech_region = os.getenv("SPEECH_REGION")
keyword_model_file = os.getenv("KEYWORD_MODEL_FILE")
keyword = os.getenv("KEYWORD")
BROKER_ADDRESS = os.getenv("BROKER_ADDRESS")
PORT = int(os.getenv("PORT", 1883))  # Default to 1883 if not set
MQTT_TOPIC = os.getenv("MQTT_TOPIC")
MESSAGE = os.getenv("MESSAGE")
api_type = os.getenv("API_TYPE")
api_key = os.getenv("API_KEY")
api_base = os.getenv("API_BASE")
model = os.getenv("MODEL")
api_version = os.getenv("API_VERSION")
# Set your OpenAI API key
openai.api_key = os.getenv("OPENAI_API_KEY")


client = AzureOpenAI(
        api_key=api_key,
        api_version=api_version,
        base_url=f"{api_base}/openai/deployments/{model}",
)

SPORT_CMD =config.SPORT_CMD

def on_connect(client, userdata, flags, rc):
    """Callback function when the client connects to the broker."""
    if rc == 0:
        print("Connected to MQTT broker successfully!")
    else:
        print(f"Failed to connect, return code {rc}")

def on_publish(client, userdata, mid):
    """Callback function when a message is published."""
    print(f"Message published with mid: {mid}")


path_to_add = os.path.abspath(os.path.join(os.path.dirname(__file__), "../python"))
if os.path.exists(path_to_add):
    sys.path.insert(0, path_to_add)
    print(f"Added {path_to_add} to sys.path")
else:
    print(f"Path {path_to_add} does not exist")


from go2_webrtc import Go2Connection

# Initialize text-to-speech engine
tts_engine = pyttsx3.init()


lock = threading.Lock()
def speak(text):
    """Speak the provided text."""
    with lock:
        #tts_engine.setProperty('rate', 400)  # Set the speech rate
        tts_engine.say(text)
        tts_engine.runAndWait()
        #threading.Thread(target=tts_engine.runAndWait).start()
    #tts_engine.runAndWait()

def playAudio(audio_file):
    # Open the file using wave module
    with wave.open(audio_file, 'rb') as wf:
        # Read the audio data
        audio_data = wf.readframes(wf.getnframes())
        # Convert the byte data to numpy array
        import numpy as np
        audio_data = np.frombuffer(audio_data, dtype=np.int16)

        # Play the audio data without blocking
        sd.play(audio_data, wf.getframerate())

# Function to record audio
def record_audio(filename, duration=5, samplerate=16000):
    print(f"Recording for {duration} seconds...")
    audio_data = sd.rec(int(duration * samplerate), samplerate=samplerate, channels=1, dtype='int16')
    sd.wait()  # Wait until recording is finished
    print("Recording complete!")
    # Save audio as a WAV file
    wav.write(filename, samplerate, audio_data)
    print(f"Saved recording to {filename}")

# Function to listen for a keyword
def listen_for_keyword(keyword="hello"):
    recognizer = sr.Recognizer()
    while True:
        print(f"Listening for the keyword: '{keyword}'...")
        record_audio("keyword_detection.wav", duration=3)
        try:
            # Process the audio with SpeechRecognition
            with sr.AudioFile("keyword_detection.wav") as source:
                audio_data = recognizer.record(source)
                detected_text = recognizer.recognize_google(audio_data).lower()
                print(f"Detected: {detected_text}")
                if keyword.lower() in detected_text:
                    print(f"Keyword '{keyword}' detected!")
                    speak("Please Say your command.")
                    return
        except sr.UnknownValueError:
            pass  # Ignore unrecognized audio
        except Exception as e:
            print(f"Error: {e}")


# Function to transcribe speech (google)
def transcribe_speech():
    recognizer = sr.Recognizer()
    print("Listening for your question...")
    record_audio("speech_transcription.wav", duration=5)
    speak("OK, I will do the command now.")
    try:
        with sr.AudioFile("speech_transcription.wav") as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data)
            print(f"Transcribed Text: {text}")
            return text
    except sr.UnknownValueError:
        print("Sorry, I couldn't understand the speech.")
        speak("Sorry, I couldn't understand what you said. Please try again.")
    except Exception as e:
        print(f"Error: {e}")
    return None

# Function to transcribe speech with (whisper edge)
def transcribe_whisper(model):    

    # VAD setup
    vad = webrtcvad.Vad()
    vad.set_mode(3)  # Set VAD aggressiveness: 0-3

    # Audio settings
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000  # Must be one of the supported rates
    CHUNK = int(RATE * 0.02)  # 20ms chunks

    # Silence detection settings
    SILENCE_THRESHOLD = 30  # Number of consecutive silent chunks (~200ms)

    # Initialize PyAudio
    audio = pyaudio.PyAudio()

    stream = audio.open(format=FORMAT, channels=CHANNELS,
                        rate=RATE, input=True,
                        frames_per_buffer=CHUNK)

    print("Listening with VAD...")
    time.sleep(1.5)
    start_time = time.time()
    

    audio_buffer = []
    is_speaking = False
    silence_count = 0

    try:
        while True:
            # Read a 20ms chunk
            audio_data = stream.read(CHUNK, exception_on_overflow=False)
            
            # Check if it's speech
            if vad.is_speech(audio_data, RATE):
                audio_buffer.append(audio_data)
                is_speaking = True
                silence_count = 0  # Reset silence count when speech is detected
            elif is_speaking:
                silence_count += 1

                # Check if silence duration exceeds threshold
                if silence_count > SILENCE_THRESHOLD:
                    print("Processing...")
                    start_time = time.time()

                    # Combine all buffered audio into a single array
                    combined_audio = b''.join(audio_buffer)
                    audio_buffer = []  # Reset buffer

                    # Convert to NumPy array for Whisper
                    np_audio = np.frombuffer(combined_audio, dtype=np.int16).astype(np.float32) / 32768.0

                    # Transcribe with Whisper
                    result = model.transcribe(np_audio, fp16=True)
                    print(f"Transcription: {result['text']}")  
                    end_time = time.time() 
                    
                    time_difference = end_time - start_time
                    print(f"Whisper Time taken: {time_difference} seconds")                  
                    is_speaking = False
                    silence_count = 0  # Reset silence count
                    return result['text']
    except KeyboardInterrupt:
        print("Stopping...")
        stream.stop_stream()
        stream.close()
        audio.terminate()


# Function to map GPT-4 response to SPORT_CMD
def map_to_sport_cmd(response):
    for cmd_id, cmd_name in SPORT_CMD.items():
        if cmd_name.lower() in response.lower():
            print(f"Matched Command: {cmd_name} (ID: {cmd_id})")
            return cmd_id, cmd_name
    print(f"No matching command found for response: {response}")
    return None, None

def gen_command(cmd: int):
        command = {
            "type": "msg",
            "topic": "rt/api/sport/request",
            "data": {
                "header": {"identity": {"id": Go2Connection.generate_id(), "api_id": cmd}},
                "parameter": json.dumps(cmd),
            },
        }
        command = json.dumps(command)
        return command

def gen_mov_command(x: float, y: float, z: float):

    command = {
        "type": "msg",
        "topic": "rt/api/sport/request",
        "data": {
            "header": {"identity": {"id": Go2Connection.generate_id(), "api_id": 1008}},
            "parameter": json.dumps({"x": x, "y": y, "z": z}),
        },
    }
    command = json.dumps(command)
    return command


# Function to send the command
def send_command_to_robot(mqtt_bridge, cmd_id, cmd_name, movement=None):
    """
    Send a command to the robot. If movement data is provided, send a movement command.
    """
    if movement:
        # If movement data exists, create and send the movement command
        x, y, z = movement["x"], movement["y"], movement["z"]
        command = gen_mov_command(x, y, z)
        print(f"Sending Movement Command: {command}")
        mqtt_bridge.publish(MQTT_TOPIC, command)
        print(f"Published Movement Command: {command}")
    else:
        # If it's a standard SPORT_CMD, send that command
        print(f"Sending Command to Robot: {cmd_name} (ID: {cmd_id})")
        command = gen_command(cmd_id)
        mqtt_bridge.publish(MQTT_TOPIC, command)
        print(f"Published MQTT Command: {command}")


def resample_audio(audio_data, original_sample_rate, target_sample_rate):
    number_of_samples = round(len(audio_data) * float(target_sample_rate) / original_sample_rate)
    resampled_audio = resample(audio_data, number_of_samples)
    return resampled_audio.astype(np.int16)


async def send_audio(client: RTClient, audio_file_path: str):
    sample_rate = 24000
    duration_ms = 100
    samples_per_chunk = sample_rate * (duration_ms / 1000)
    bytes_per_sample = 2
    bytes_per_chunk = int(samples_per_chunk * bytes_per_sample)

    extra_params = (
        {
            "samplerate": sample_rate,
            "channels": 1,
            "subtype": "PCM_16",
        }
        if audio_file_path.endswith(".raw")
        else {}
    )

    audio_data, original_sample_rate = sf.read(audio_file_path, dtype="int16", **extra_params)

    if original_sample_rate != sample_rate:
        audio_data = resample_audio(audio_data, original_sample_rate, sample_rate)

    # Start playing the entire audio before sending chunks
    print("Playing input audio...")
    sd.play(audio_data, samplerate=sample_rate, blocking=True)

    audio_bytes = audio_data.tobytes()
   
    for i in range(0, len(audio_bytes), bytes_per_chunk):
        chunk = audio_bytes[i : i + bytes_per_chunk]
        # Play the current audio chunk in real-time
        #audio_array = np.frombuffer(chunk, dtype=np.int16)
        #sd.play(audio_array, samplerate=sample_rate, blocking=False)
        await client.send_audio(chunk)


async def send_audio_from_microphone_vad(client: RTClient, vad_mode=3, silence_duration=1, sample_rate=16000):
    """
    Capture audio from the microphone and stop when no speech (VAD-based silence) is detected for a specified duration.

    Args:
        client (RTClient): The real-time client to send audio.
        vad_mode (int): Aggressiveness mode for VAD (0-3). Higher is more aggressive in filtering silence.
        silence_duration (float): Duration (seconds) of continuous silence to stop recording.
        sample_rate (int): Target sample rate for recording.
    """
    assert sample_rate in [8000, 16000, 32000, 48000], "VAD only supports 8000, 16000, 32000, or 48000 Hz sample rates."
    
    vad = webrtcvad.Vad(vad_mode)  # Initialize VAD
    duration_ms = 30  # Chunk duration in milliseconds
    samples_per_chunk = int(sample_rate * (duration_ms / 1000))
    silence_chunks_required = silence_duration / (duration_ms / 1000)  # Total silent chunks to stop

    silence_counter = 0  # Counter for consecutive silent chunks

    print("Starting microphone stream...")
    try:
        with sd.InputStream(
            samplerate=sample_rate, channels=1, dtype="int16", blocksize=samples_per_chunk
        ) as stream:
            while True:
                    # Read audio chunk
                    audio_chunk, _ = stream.read(samples_per_chunk)
                    audio_bytes = audio_chunk.tobytes()

                    # Check if speech is present using VAD
                    is_speech = vad.is_speech(audio_bytes, sample_rate)
                    
                    if is_speech:
                        silence_counter = 0  # Reset counter if speech is detected
                    else:
                        silence_counter += 1  # Increment silence counter if no speech
                    print("Silence counter\n")
                    print(silence_counter)
                    # Stop if silence duration threshold is reached
                    if silence_counter >= silence_chunks_required:
                        print("Silence detected (VAD). Stopping microphone stream.")
                        break

                    # Send audio data
                    #if is_speech:  # Only send audio when speech is detected
                    print("Sending Audio Bytes")
                    await client.send_audio(audio_bytes)
                    

    except Exception as e:
        print(f"Error capturing audio from microphone: {e}")
                #break
    finally:
        print("Microphone stream stopped. Ready for next action.")


async def send_audio_from_microphone_fixed(client: RTClient, capture_duration=3):
    sample_rate = 16000  # Target sample rate
    duration_ms = 30  # Chunk duration in milliseconds
    samples_per_chunk = int(sample_rate * (duration_ms / 1000))
    bytes_per_sample = 2  # 16-bit samples
    bytes_per_chunk = samples_per_chunk * bytes_per_sample
    global start_time  # Use the global keyword to modify the variable

    print("Starting microphone stream...")
    #record_audio("Transcript_detection.wav", duration=3)
    start_time = time.time()
    with sd.InputStream(
        samplerate=sample_rate, channels=1, dtype="int16", blocksize=samples_per_chunk
    ) as stream:
        while True:
            try:
                current_time = time.time()
                if current_time - start_time > capture_duration:
                    print("Capture duration reached. Stopping microphone stream.")
                    start_time = time.time()
                    break
                audio_chunk, _ = stream.read(samples_per_chunk)
                audio_bytes = audio_chunk.tobytes()
                await client.send_audio(audio_bytes)
            except Exception as e:
                print(f"Error capturing audio from microphone: {e}")
                break

async def send_audio_from_microphone(client):
    """
    Continuously listen to the microphone and send audio to the client when speech is detected.
    """
    # VAD Configuration
    vad = webrtcvad.Vad(2)  # Aggressiveness: 0 (least) to 3 (most sensitive)
    sample_rate = 16000  # VAD works best at 16kHz sample rate
    chunk_duration = 0.03  # 30ms chunk duration
    samples_per_chunk = int(sample_rate * chunk_duration)
    
    print("Starting microphone stream... Listening for speech...")
    buffer = []
    speech_detected = False
    silence_duration = 1.0  # Stop sending audio after 1 second of silence
    last_speech_time = None

    def is_speech(audio_chunk):
        """Check if the chunk contains speech using VAD."""
        return vad.is_speech(audio_chunk, sample_rate)

    with sd.InputStream(samplerate=sample_rate, channels=1, dtype="int16", blocksize=samples_per_chunk) as stream:
        while True:
            try:
                # Read a chunk of audio
                audio_chunk, _ = stream.read(samples_per_chunk)
                audio_bytes = audio_chunk.tobytes()

                if is_speech(audio_bytes):  # Speech detected
                    if not speech_detected:
                        print("Speech detected. Recording...")
                        speech_detected = True
                        buffer = []  # Clear previous buffer

                    last_speech_time = time.time()
                    buffer.append(audio_bytes)  # Add audio chunk to buffer

                elif speech_detected:
                    # Check if silence duration has passed
                    if time.time() - last_speech_time > silence_duration:
                        print("Speech ended. Sending audio to server...")
                        speech_detected = False
                        # Send the buffered audio
                        if buffer:
                            audio_data = b''.join(buffer)
                            #resample_audio(audio_data, sample_rate, 24000)
                            await client.send_audio(audio_bytes)
                            print(f"Sent audio: {len(audio_data)} bytes")
                        buffer = []  # Clear buffer after sending
            except Exception as e:
                print(f"Error capturing or processing audio: {e}")
                break


def check_gpt4o_response(response_text):
    # Check if the response is related to movement (in {x, y, z} format)
        if "{" in response_text and "}" in response_text:
            print("move command")
            # Check if it contains a valid direction command like {'x': 0.22, 'y': -0.22, 'z': 0}
            # Extract the content inside curly braces using a regular expression
            match = re.search(r'\{.*?\}', response_text)
            if match:
                try:
                    # Parse the extracted JSON-like structure
                    move_command = json.loads(match.group(0))
                    print("Extracted move command:", move_command)
                    # Check if it contains valid movement keys
                    if all(key in move_command for key in ['x', 'y', 'z']):
                        return move_command  # Return the valid movement command
                except json.JSONDecodeError as e:
                    print(f"Error parsing move command: {e}")
        
        # If no valid move command, map to SPORT_CMD
        print("No movement command detected, mapping to SPORT_CMD...")
        
        # Get the closest match from SPORT_CMD
        for cmd_id, cmd_name in SPORT_CMD.items():
            if cmd_name.lower() in response_text.lower():
                print(f"Matched Command: {cmd_name} (ID: {cmd_id})")
                return cmd_name  # Return the SPORT_CMD name
        
        # If no match is found, return a default response
        return "Sorry, I couldn't recognize the command."

async def receive_message_item(item: RTMessageItem, out_dir: str):
    print(start_time)
    prefix = f"[response={item.response_id}][item={item.id}]"
    async for contentPart in item:
        if contentPart.type == "audio":

            async def collect_audio(audioContentPart: RTAudioContent):
                audio_data = bytearray()
                async for chunk in audioContentPart.audio_chunks():
                    audio_data.extend(chunk)
                    # Play the audio chunk in real-time
                    #audio_array = np.frombuffer(chunk, dtype=np.int16)
                    #sd.play(audio_array, samplerate=24000, blocking=False)
                return audio_data

            async def collect_transcript(audioContentPart: RTAudioContent):
                audio_transcript: str = ""
                async for chunk in audioContentPart.transcript_chunks():
                    audio_transcript += chunk
                return audio_transcript

            audio_task = asyncio.create_task(collect_audio(contentPart))
            transcript_task = asyncio.create_task(collect_transcript(contentPart))
            audio_data, audio_transcript = await asyncio.gather(audio_task, transcript_task)
            print(prefix, f"Audio received with length: {len(audio_data)}")
            # Start playing the entire audio before sending chunks
            print("Playing response audio...")

            # Play the audio data directly
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            print("Playing response audio directly...")
            response_time = time.time()  # Record the time when response starts playing
            if start_time is not None:
                latency = response_time - start_time

                print(f"Latency: {latency:.2f} seconds")
            sd.play(audio_array, samplerate=24000, blocking=False)

            #sd.play(audio_data, samplerate=24000, blocking=True)
            print(prefix, f"Audio Transcript: {audio_transcript}")
            
            gpt4_response = check_gpt4o_response(audio_transcript)
            
            # Step 4: Check if it's a movement command
            if isinstance(gpt4_response, dict):  # Check if it's a movement command (x, y, z)
                send_command_to_robot(mqtt_bridge, 1008, "Move", movement=gpt4_response)
            else:
                # Step 5: Map to SPORT_CMD and send the command
                cmd_id, cmd_name = map_to_sport_cmd(gpt4_response)
                if cmd_id:
                    send_command_to_robot(mqtt_bridge, cmd_id, cmd_name)
                else:
                    print("Sorry, I couldn't recognize the command.just chat")



            with open(os.path.join(out_dir, f"{item.id}_{contentPart.content_index}.wav"), "wb") as out:
                audio_array = np.frombuffer(audio_data, dtype=np.int16)
                sf.write(out, audio_array, samplerate=24000)
                #sd.play(audio_array, samplerate=24000, blocking=False)
            with open(
                os.path.join(out_dir, f"{item.id}_{contentPart.content_index}.audio_transcript.txt"),
                "w",
                encoding="utf-8",
            ) as out:
                out.write(audio_transcript)
            #print("Playing response audio...")
            extra_params = {}

            # Adjust the read function based on file type
            #audio_data, samplerate = sf.read( f"{item.id}_{contentPart.content_index}.wav", dtype="int16", **extra_params)
            #sd.play(audio_data, samplerate=samplerate, blocking=True)


        elif contentPart.type == "text":
            text_data = ""
            async for chunk in contentPart.text_chunks():
                text_data += chunk
            print(prefix, f"Text: {text_data}")
            with open(
                os.path.join(out_dir, f"{item.id}_{contentPart.content_index}.text.txt"), "w", encoding="utf-8"
            ) as out:
                out.write(text_data)


async def receive_function_call_item(item: RTFunctionCallItem, out_dir: str):
    prefix = f"[function_call_item={item.id}]"
    await item
    print(prefix, f"Function call arguments: {item.arguments}")
    with open(os.path.join(out_dir, f"{item.id}.function_call.json"), "w", encoding="utf-8") as out:
        out.write(item.arguments)


async def receive_response(client: RTClient, response: RTResponse, out_dir: str):
    prefix = f"[response={response.id}]"
    async for item in response:
        print(prefix, f"Received item {item.id}")
        if item.type == "message":
            asyncio.create_task(receive_message_item(item, out_dir))
        elif item.type == "function_call":
            asyncio.create_task(receive_function_call_item(item, out_dir))

    print(prefix, f"Response completed ({response.status})")
    if response.status == "completed":
        await client.close()


async def receive_input_item(item: RTInputAudioItem):
    prefix = f"[input_item={item.id}]"
    await item
    print(prefix, f"Transcript: {item.transcript}")
    print(prefix, f"Audio Start [ms]: {item.audio_start_ms}")
    print(prefix, f"Audio End [ms]: {item.audio_end_ms}")


async def receive_events(client: RTClient, out_dir: str):
    async for event in client.events():
        if event.type == "input_audio":
            asyncio.create_task(receive_input_item(event))
        elif event.type == "response":
            asyncio.create_task(receive_response(client, event, out_dir))


async def receive_messages(client: RTClient, out_dir: str):
    await asyncio.gather(
        receive_events(client, out_dir),
    )

# Function to get GPT-4 response
def get_gpt4_response(prompt):
    """
    Use GPT-4 to map the user input directly to the SPORT_CMD descriptions.
    """
    print("Starting to send transcript to GPT4")
    start_time = time.time()
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": config.SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2000
        )
        end_time = time.time()        
        # Extract and clean the response
        response_text = response.choices[0].message.content.strip()
        time_difference = end_time - start_time
        print(f"Time taken: {time_difference} seconds")
        print(response_text)
        
        # Check if the response is related to movement (in {x, y, z} format)
        if "{" in response_text and "}" in response_text:
            print("move command")
            # Check if it contains a valid direction command like {'x': 0.22, 'y': -0.22, 'z': 0}
            try:
                # Try parsing the response as JSON-like format for movement
                move_command = json.loads(response_text)
                
                print(move_command)
                if 'x' in move_command and 'y' in move_command and 'z' in move_command:
                    return move_command  # Return the movement command if valid
            except Exception as e:
                print(f"Error parsing move command: {e}")
        
        # If no valid move command, map to SPORT_CMD
        print("No movement command detected, Chat with users...")
        return response_text
    except Exception as e:
        print(f"Error communicating with OpenAI: {e}")
        return None
    

# Function to get DeepSeek-R1 response
def get_deepseek_NIM_response(prompt):
    try:
        client = AzureOpenAI(
        base_url = "https://integrate.api.nvidia.com/v1",
        api_key = "$API_KEY_REQUIRED_IF_EXECUTING_OUTSIDE_NGC"
        )

        response = client.chat.completions.create(
        model="deepseek-ai/deepseek-r1",
        messages=[
                    {"role": "system", "content": config.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
        temperature=0.6,
        top_p=0.7,
        max_tokens=4096
        )

        response_text = response.choices[0].message.content.strip()
        print(response_text)
        return response_text
    except Exception as e:
        print(f"Error communicating with DeepSeek R1 NIM: {e}")
        return None
    
async def run(client: RTClient, audio_file_path: str, out_dir: str):
    #speak("Yes,I am here.")
    #playAudio("./alert6.wav")
    print("Configuring Session...", end="", flush=True)
    await client.configure(
        instructions=config.SYSTEM_PROMPT,
        #turn_detection=NoTurnDetection(),
        turn_detection=ServerVAD(threshold=0.5, prefix_padding_ms=300, silence_duration_ms=200),
        input_audio_transcription=InputAudioTranscription(model="whisper-1"),
    )
    print("Done")
    
    await asyncio.gather(send_audio_from_microphone_fixed(client), receive_messages(client, out_dir))


def get_env_var(var_name: str) -> str:
    value = os.environ.get(var_name)
    if not value:
        raise OSError(f"Environment variable '{var_name}' is not set or is empty.")
    return value


async def with_azure_openai(audio_file_path: str, out_dir: str):
    endpoint = get_env_var("AZURE_OPENAI_ENDPOINT")
    key = get_env_var("AZURE_OPENAI_API_KEY")
    deployment = get_env_var("AZURE_OPENAI_DEPLOYMENT")
    async with RTClient(url=endpoint, key_credential=AzureKeyCredential(key), azure_deployment=deployment) as client:
        await run(client, audio_file_path, out_dir)


async def with_openai(audio_file_path: str, out_dir: str):
    key = get_env_var("OPENAI_API_KEY")
    model = get_env_var("OPENAI_MODEL")
    async with RTClient(key_credential=AzureKeyCredential(key), model=model) as client:
        await run(client, audio_file_path, out_dir)



def keyword_wake():
    # Set up the speech configuration and audio input
    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=speech_region)
    audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)

    # Load the keyword recognition model
    keyword_model = speechsdk.KeywordRecognitionModel(keyword_model_file)

    # Create the recognizer
    recognizer = speechsdk.KeywordRecognizer(audio_config=audio_config)

    print(f"Listening continuously for the keyword: '{keyword}'... (Press Ctrl+C to stop)")
    #record_audio("keyword_detection_clean.wav", duration=3)

    try:
        while True:
            # Start keyword recognition
            result = recognizer.recognize_once_async(model=keyword_model).get()

            # Process the recognition result
            if result.reason == speechsdk.ResultReason.RecognizedKeyword:
                print(f"Keyword recognized: {result.text}")
                recognizer.stop_recognition_async()
                playAudio("./8378.wav")
                break
            else:
                print(f"Keyword not recognized. Reason: {result.reason}")
    except KeyboardInterrupt:
        print("\nStopping keyword recognition.")
    except Exception as e:
        print(f"Error occurred: {e}")

if __name__ == "__main__":
     # Initialize the MQTT bridge for remote control 
    mqtt_bridge = mqtt.Client()
    # Assign callback functions
    mqtt_bridge.on_connect = on_connect
    mqtt_bridge.on_publish = on_publish
    # Connect to the MQTT broker
    print(f"Connecting to MQTT broker at {BROKER_ADDRESS}:{PORT}...")
    mqtt_bridge.connect(BROKER_ADDRESS, PORT, keepalive=60)
    # Load Whisper model
    # Check if CUDA is available
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(device)
    model = whisper.load_model("base").to(device)
    load_dotenv()
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <out_dir> [whisper|gpt4o]")
        print("If the third argument is not provided, it will default to whisper")
        sys.exit(1)

    file_path = ""
    out_dir = sys.argv[1]
    provider = sys.argv[2] if len(sys.argv) == 3 else "whisper"

    if not os.path.isdir(out_dir):
        print(f"Directory {out_dir} does not exist")
        sys.exit(1)

    if provider not in ["whisper", "gpt4o"]:
        print(f"Provider {provider} needs to be one of 'whisper' or 'gpt4o'")
        sys.exit(1)

    if provider == "whisper":
        print("Whisper Mode")
        print("Sending to GPT-4...")
        while True:
            keyword_wake()
            user_input = transcribe_whisper(model)
            if not user_input:
                continue
            gpt4_response = get_gpt4_response(user_input) 
            #gpt4_response = get_deepseek_NIM_response(user_input)
            if gpt4_response:
                print("GPT-4 Response:")
                print(gpt4_response)
                                
                # Step 4: Check if it's a movement command
                if isinstance(gpt4_response, dict):  # Check if it's a movement command (x, y, z)
                    #send_command_to_robot(mqtt_bridge, 1008, "Move", movement=gpt4_response)
                    print("Movement Command , No speaking")
                    speak("OK")
                else:
                    speak(gpt4_response)                  
            else:
                print("No response from GPT-4.")
                speak("Sorry, I couldn't get a response. Please try again.")
    
    else : 
        print("GPT4o realtime Mode")
        while True:
            #listen_for_keyword(keyword="hello")
            keyword_wake()
            print("Starting new recording session...")
            asyncio.run(with_azure_openai(file_path, out_dir))
            print("Recording session ended. Waiting before restarting...")
