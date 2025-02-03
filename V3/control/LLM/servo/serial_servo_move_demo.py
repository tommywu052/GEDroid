#!/usr/bin/env python3
# encoding: utf-8
import sys
import time
sys.path.append("..")
import hiwonder_servo_controller

servo_control = hiwonder_servo_controller.HiwonderServoController('/dev/ttyTHS1', 115200)

print('serial servo move between 500 - 1000')

while True:
    try:
        servo_id = 6  # Servo id(0-253)
        position = 500  # Position(0-1000)
        duration = 500  # Timing(20-30000)
        servo_control.set_servo_position(servo_id, position, duration)
        time.sleep(duration/1000.0 + 0.1)  # 完全转到位需要多100ms, 因为数据传输需要时间
        
        servo_id = 6
        position = 1000
        duration = 500
        servo_control.set_servo_position(servo_id, position, duration)
        time.sleep(duration/1000.0 + 0.1)
    except KeyboardInterrupt:
        servo_id = 6
        position = 0
        duration = 500
        servo_control.set_servo_position(servo_id, position, duration)
        break
