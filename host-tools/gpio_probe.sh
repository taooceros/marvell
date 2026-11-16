#!/bin/bash
# 逐根测试卡上 CP2105 的 GPIO：拉低 0.5 秒再写回 1，然后看单片机/CN106 串口和主机 PCIe 有没有反应。
# 用法：bash /tmp/gpio_probe.sh   （在你自己的终端里跑，会问 sudo 密码）
Z="timeout 5 zellij -s dpu action"
LOG0=/tmp/dpu_ttyUSB0.log   # CN106 控制台（screen -L 日志，约 10 秒刷新一次）
LOG1=/tmp/dpu_ttyUSB1.log   # 单片机控制台
PCI=/sys/bus/pci/devices/0000:99:00.0/config

pcie() { [ -e "$PCI" ] && od -An -tx2 -N2 "$PCI" | tr -d ' ' || echo gone; }

for l in "2 0" "2 1" "2 2" "1 0" "1 1"; do
  set -- $l
  read -r -p "回车：测 gpiochip$1 line $2（输入 q 退出）" a
  [ "$a" = q ] && break

  # 先让卡上 Linux 把缓存写盘，万一这根线会复位 CN106
  $Z write-chars -p terminal_3 'sync' && $Z write -p terminal_3 13
  sleep 2

  s0=$(stat -c %s $LOG0); s1=$(stat -c %s $LOG1)
  echo "$(date +%T) 拉低 gpiochip$1 line $2 ..."
  sudo gpioset --mode=time --usec=500000 gpiochip$1 $2=0
  sudo gpioset gpiochip$1 $2=1
  echo "$(date +%T) 已写回 1，等 12 秒看反应 ..."
  sleep 12

  new1=$(tail -c +$((s1 + 1)) $LOG1 | tr -d '\r')
  new0=$(tail -c +$((s0 + 1)) $LOG0 | tr -d '\r')
  echo "  单片机串口新输出: $(printf '%s' "$new1" | wc -c) 字节"
  echo "  CN106 串口新输出: $(printf '%s' "$new0" | wc -c) 字节"
  echo "  主机 PCIe 99:00.0 vendor: $(pcie)（177d 正常）"

  if printf '%s' "$new1" | grep -q 'Booting Zephyr'; then
    echo ">>> 找到了：gpiochip$1 line $2 复位了单片机。停止测试。"
    break
  fi
  if printf '%s' "$new0" | grep -q 'SCP BL1'; then
    echo ">>> gpiochip$1 line $2 复位了 CN106（卡上 Linux 会重启，约 90 秒）。"
  fi
  if [ "$(pcie)" != "177d" ]; then
    echo ">>> 主机上 PCIe 设备掉了，停止测试，请拔主机电源线断电恢复。"
    break
  fi
done
