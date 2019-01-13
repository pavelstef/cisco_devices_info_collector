# Idea
This script was written to collect inventory information from Cisco networking equipment.
#
# Information about script
The script supports the following types of equipment:
#
• Cisco IOS and IOS-XE devices. (always works well)

• Cisco WLC. (always works well)

• Cisco SMB (SG300). (does not always work well, sometimes does not work)

• Cisco SMB (SG350, SG500). (50/50 - but sometimes it works)
#
The script saves the following useful information to the “inventory.xlsx” file:
#
• Ip address

• device type

• hostname

• pid

• sn

• os

• uptime (* not for Cisco SMB)
#
In addition, the script saves the full output of commands to a separate text file "raw_parsed_output.txt"
#
At runtime, the script uses the following commands:
#
• Cisco IOS and IOS-XE: 'show cdp neighbors', 'show inventory', 'show version'

• Cisco WLC:: 'show cdp neighbors detail', 'show inventory', 'show sysinfo'
#
# Requirements
The script requires the following Python modules:
#
•	import time     # To pauses

•	import re       # To regex

•	import ipaddress  # Module for work with IP address range

•	import platform     # To check OS type

•	import subprocess   # To run OS commands, such "ping"

•	from concurrent.futures import ThreadPoolExecutor, as_completed   # Parallel work some processes

•	import getpass      # To input password in safe mode

•	from netmiko import ConnectHandler, SSHDetect      # To work with network equipment

•	import paramiko     # To work with network equipment and determine the type of equipment manually

•	import textfsm      # To parse output of equipments

•	from pprint import pprint   # To usable print output

•	from tabulate import tabulate    # To usable table output

•	import xlsxwriter   # To save the files in Excel format
#
Make sure that you have all the necessary modules installed before you run the script.
To get information about how to install modules in Python see
https://docs.python.org/3/installing/index.html

# Future plans
1) Make a visual map of the network based on information about CDP neighbors
....
