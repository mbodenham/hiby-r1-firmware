insmod i2c_gpio_add.ko
cd /sys/module/i2c_gpio_add/parameters/
echo bus_num=3 rate=200000 scl=PB30 sda=PB31  > i2c_bus
