insmod leds_pwm_add.ko  \
	led_pwm0_gpio=PC01 led_pwm0_name=red led_pwm0_trigger=breathing led_pwm0_max_brightness=100 led_pwm0_period_ns=100000  \
	led_pwm1_gpio=PC02 led_pwm1_name=blue led_pwm1_trigger=none led_pwm1_max_brightness=100 led_pwm1_period_ns=100000  \

