#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  2019 Pavel Stefanenko <p.stefanenko@gmail.com>
#  Python 3.7.1
#
__author__ = 'Pavel Stefanenko'

"""
    This script was written to collect inventory information from Cisco networking equipment.

    The script supports the following types of equipment:
        • Cisco IOS and IOS-XE devices. (always works well)
        • Cisco WLC. (always works well)
        • Cisco SMB (SG300). (does not always work well, sometimes does not work)
        • Cisco SMB (SG350, SG500). (50/50 - but sometimes it works)

    The script saves the following useful information to the “inventory.xlsx” file:
        • Ip address
        • device type
        • hostname
        • pid
        • sn
        • os
        • uptime (* not for Cisco SMB)

    In addition, the script saves the full output of commands to a separate text file "raw_parsed_output.txt"

    At runtime, the script uses the following commands:
        • Cisco IOS and IOS-XE, Cisco SMB : 'show cdp neighbors', 'show inventory', 'show version'
        • Cisco WLC:: 'show cdp neighbors detail', 'show inventory', 'show sysinfo'
"""


### Import modules

import time     # To pauses
import re       # To regex
import ipaddress  # Module for work with IP address range
import platform     # To check OS type
import subprocess   # To run OS commands, such "ping"
from concurrent.futures import ThreadPoolExecutor, as_completed   # Parallel work some processes
import getpass      # To input password in safe mode
from netmiko import ConnectHandler, SSHDetect      # To work with network equipment
import paramiko     # To work with network equipment and determine the type of equipment manually
import textfsm      # To parse output of equipments
from pprint import pprint   # To usable print output
from tabulate import tabulate    # To usable table output
import xlsxwriter   # To save the files in Excel format


### Functions

def welcome_message():
    """
    Function which print welcome message and ask status of verbove_flag
    :return: Verbose flag [True or False (dafault)], This flag defines the output of intermediate information
            about the progress of the script.
    """

    print('=' * 50)
    print('Welcome to the script that compiles the information from the Cisco device on your site!')
    print('Would you like to see all log information during work of the script ? {Y/N} N (default)')
    verbose = input('>: ')
    if verbose is 'Y' or verbose is 'Yes' or verbose is 'y' or verbose is 'yes':
        print('Verbose flag set to True', '\n')
        return (True, True)
    else:
        print('Verbose flag set to False', '\n')
        return (True, False)

def request_ip_address_list(verbose_flag):
    """
    Function which ask user to enter IP address (IPv4) list or range, and checks the address type.
    We need IPv4 addresses private type only.
    :param verbose_flag: This flag defines the output of intermediate information about the progress of the script.
    :return: List of IPv4 addresses of devices
    """

    print('Enter IPv4 address list (192.168.1.1, 192.168.1.2, ...) or range (10.1.1.1-3)')
    print('If You want to use range more than one octet, please, split it on different ranges'
          ' (10.1.1.1-10.1.2.200 = 10.1.1.1-255, 10.1.2.1-200)')
    unvalidated_ip_list = input('>: ')

    # Normalize element of unvalidated_ip_list
    unvalidated_ip_list = unvalidated_ip_list.split(',')
    i=0
    while i < len(unvalidated_ip_list):
        if unvalidated_ip_list[i] is ' ':
            unvalidated_ip_list.remove(unvalidated_ip_list[i])
        else:
            unvalidated_ip_list[i] = str(unvalidated_ip_list[i]).strip()
        i +=1

    # We need be sure then list contain ip address only, without letters
    ip_list = []
    for i in range(len(unvalidated_ip_list)):
        match = None
        match = re.search("(\d+.\d+.\d+.\d+-\d+.\d+.\d+.\d+)|(\d+.\d+.\d+.\d+-\d+)|(\d+.\d+.\d+.\d+)", unvalidated_ip_list[i])
        if match is None:
            if verbose_flag:
                print('\n', 'Your input "{}" looks wrong, this element will delete from list of IP'.format(unvalidated_ip_list[i]))
        else:
            ip_list.append(unvalidated_ip_list[i])

    if len(ip_list) is 0:
        return (False, ip_list)

    del(unvalidated_ip_list)

    # Finding the ranges among the elements of the list, and replacing this ranges with the list of IPs
    for i in range(len(ip_list)):
        for ip in ip_list:
            if '-' in ip:  # If range
                index_ip = ip_list.index(ip)
                temp = ip.strip().split('-')

                ip_first = temp[0].strip().split('.')
                ip_final = temp[1].strip().split('.')

                # We cannot have more than 255 hosts in the network
                if int(ip_final[-1]) > 255:
                    ip_final[-1] = '255'

                count_hosts_minus_one = int(ip_final[-1]) - int(ip_first[-1])

                ip_first = ipaddress.ip_address((temp[0]).strip())
                ip_final = ip_first + count_hosts_minus_one

                ip_adr = ip_first
                ip_list_temp = []
                while ip_adr <= ip_final:
                    ip_list_temp.append(str(ip_adr))
                    ip_adr +=1

                ip_list.pop(index_ip)
                ip_list.extend (ip_list_temp)



    # Checking IP addresses for membership of group Private IPv4,
    # based on https://www.iana.org/assignments/iana-ipv4-special-registry/iana-ipv4-special-registry.xhtml
    for ip in ip_list:
        try:
            ip = ipaddress.ip_address(ip)
            if ip.is_private is False:
                if verbose_flag:
                    print('\n','Ip address {}, which you entered is not private IPv4 address. This address will be removed from list'.format(ip))
                ip_list.remove(str(ip))
        except ValueError:
            if verbose_flag:
                print('\n',
                      'Ip address {}, which you entered is not private IPv4 address. This address will be removed from list'.format(ip))
            ip_list.remove(str(ip))

    # We need be sure to have one or more valid IP in the list
    if len(ip_list) is 0:
        return (False, ip_list)
    else:
        return (True, ip_list)



def check_ip_availability(list_ip_for_checking, verbose_flag, limit=3):
    """
    Function to check availability of IP addresses from list
    :param list_ip_for_checking: list of IP addresses for cheking
    :param verbose_flag: This flag defines the output of intermediate information about the progress of the script.
    :param limit:
    :return: List of alive IPs
    """

    def threads_connections(function, devices, limit=limit):
        """
        Function to run another function in parallel threads
        :param function: Target function (ping in our case)
        :param devices: List of IP address
        :param limit: Count of parallel threads
        :return:
        """
        with ThreadPoolExecutor(max_workers=limit) as executor:
            futures_result = executor.map(function, devices)
        return list(futures_result)

    def check_dead_ip(item):
        """
        Function which pings IPs, and return IP if it is unreachable
        :param item: IP address of device
        :return: IP address if it is unreachable
        """
        if verbose_flag:
            print('\n', 'Checking the availability of IP {}'.format(item))

        # Checking the OS version and use the ping command format depending on the OS version
        if platform.system() is 'Linux':
            reply = subprocess.run(['ping', '-c', '3', '-n', item], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        elif platform.system() is 'Windows':
            reply = subprocess.run(['ping', '-n', '3', item], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        else:
            raise ValueError('\n', 'Something went wrong! I don\'t have this operating system for test my script')
            return(False, list_ip_for_checking)


        if reply.returncode != 0:
            return item

    list_of_unreacheble_ips = threads_connections(check_dead_ip, list_ip_for_checking)

    for item in list_of_unreacheble_ips:
        if item in list_ip_for_checking:
            list_ip_for_checking.remove(item)

    if verbose_flag:
        print('\n', 'List of available IP address')
        pprint([item for item in list_ip_for_checking])


    # We need be sure to have one or more available IP in the list
    if len(list_ip_for_checking) > 0:
        return(True,list_ip_for_checking)
    else:
        return(False, list_ip_for_checking)


def request_credentials(list_ipaddr_dev):
    """
    Function which ask user for credentials for devices and compile dictionary of devices
    :param list_ipaddr_dev: List of ip addresses of devices (from func "request_ip_address_list")
    :return: Dictionary of devices
    """

    # Ask user to enter Username and Password for devices
    print('\n','Be careful! Username and Password must be same to all devices to correct work of script.')
    print('\n', 'Enter Username for connection to devices')
    usernamse = input('Username: ')
    print('\n', 'Enter Password for connection to devices (password is not displayed when you enter)')
    password = getpass.getpass()
    print('\n', 'Enter Enable Password for connection to devices (password is not displayed when you enter)')
    enable_password = getpass.getpass()

    # Compiling dictionary of devices
    list_dic_devices = []
    for item in list_ipaddr_dev:
        list_dic_devices.append({'device_type': 'autodetect', 'ip': item, 'username': usernamse, 'password': password, 'secret': enable_password})

    return(True, list_dic_devices)


def send_command_and_get_output(list_dic_devices, command, verbose_flag, limit=3):
    """
    Function, witch send commands to devices and get output in parallel threads
    :param dlist_ic_devices: List of Dictionaries of devices parameters
    :param verbose_flag: This flag defines the output of intermediate information about the progress of the script.
    :param limit: Count of parallel threads
    :return:
    """


    def send_commands(dic_command, dic_device):
        """
        Function, witch send commands to devices and get output
        :param command: Command for send
        :param dic_device: Dictionary with parameters device for connection
        :return: Dictionary {device: output}
        """

        if verbose_flag:
            print('Connection to device: {}'.format(dic_device['ip']))

        # An attempt to understand what kind of device we are connecting to
        # For the first attempt we will use Netmiko
        try:
            guesser = SSHDetect(timeout=10, **dic_device)
            best_match = guesser.autodetect()

        except:
            if verbose_flag:
                print('\n', 'Something went wrong.'
                            ' Username or password for device {} are incorrect, or SSH is down on the device'.format(dic_device['ip']))
            return({'ip': dic_device['ip'], 'output': None, 'device_type': None})

        if best_match is not None:
            dic_device['device_type'] = best_match      # It works fine for Cisco IOS devises

        else:   # All have to do yourself :( . We are trying to determine the type of device independently.
            try:
                # We are using paramiko to connect to device
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                client.connect(hostname=dic_device['ip'], username=dic_device['username'],
                               password=dic_device['password'],
                               look_for_keys=False, allow_agent=False)

                with client.invoke_shell() as ssh:

                    result = ssh.recv(100).decode('utf-8')
                    if '(Cisco Controller)' in result:
                        # If we have connection to Cisco WLC, then we get typically WLC's welcome message
                        dic_device['device_type'] = 'cisco_wlc'
                    else:
                        # Otherwise, we have a connection to Cisco SMB
                        dic_device['device_type'] = 'cisco_s300'
            except:
                if verbose_flag:
                    print('\n', 'Something went wrong.'
                                ' Username or password for device {} are incorrect, or SSH is down on the device'.format(dic_device['ip']))
                return({'ip': dic_device['ip'], 'output': None, 'device_type': None})


            # To avoid "%AAA-I-DISCONNECT: User CLI session for user <username> over ssh , source <ip>
            #  destination <ip> TERMINATED. The Telnet/SSH session may still be connected."
            # We need to waiting
            time.sleep(3)

        # Connecting to device, sending command and getting output
        try:
            command_result = {}
            with ConnectHandler(timeout=10, **dic_device) as ssh:
                if ssh.check_config_mode is False:
                    ssh.enable()

                # Select a list of commands based on the type of device
                command_list = dic_command[dic_device['device_type']]

                # Running selected commands for device
                for command in command_list:
                    result = ssh.send_command(command)
                    result = result.strip()
                    command_result.update({command: result})

                # Determine the host name for 'cisco_ios' and 'cisco_s300' devices
                find_hostname = 'WLC'
                if dic_device['device_type'] is 'cisco_ios' or dic_device['device_type'] is 'cisco_s300':
                    find_hostname = ssh.find_prompt()
                    if '>' in find_hostname:
                        find_hostname = find_hostname.replace('>', '')
                    elif '#' in find_hostname:
                        find_hostname = find_hostname.replace('#', '')

                    find_hostname = find_hostname.strip()

                ssh.disconnect()
                return ({'ip': dic_device['ip'], 'output': command_result, 'hostname': find_hostname, 'device_type': dic_device['device_type']})

        except:
            if verbose_flag:
                print('\n', 'Something went wrong.'
                            ' Username or password for device {} are incorrect, or SSH is down on the device.'.format(dic_device['ip']))
                print('Or, you are trying to connect to an unsupported device and the SSH session cannot work correctly')
            return ({'ip': dic_device['ip'], 'output': None, 'device_type': dic_device['device_type']})


    def send_commands_threads(command_for_device, list_dic_devices, limit=limit):
        """
        Function to run another function in parallel threads
        :param command: Command for sending to device
        :param list_devices:
        :param limit: Count of parallel threads
        :return:
        """

        list_results_all_connections = []

        with ThreadPoolExecutor(max_workers=limit) as executor:
            futures_result = [executor.submit(send_commands, command_for_device, device)
                              for device in list_dic_devices]

            for f in as_completed(futures_result):
                list_results_all_connections.append(f.result())

        return list_results_all_connections


    command_to_send = command

    # We run connection in parallel threads
    result = send_commands_threads(command_to_send, list_dic_devices, limit=limit)

    # Let's do self check
    quantity_none = 0
    for item in result:
        if item['output'] is None:
            quantity_none += 1

    if quantity_none >= len(result):
        # If we have no one valid output
        return(False, result)
    else:
        # If we have at least one valid output
        return(True, result)



def parse_output_textfsm(list_of_command_output, dic_index, verbose_flag):
    """
    Function with are parsing output of devices by TextFSM
    :param list_of_command_output: List of dictionaries, which contain: device ip, hostname, type of device,
            and devise output
    :param dic_index: Compliance dictionary: device type -> command -> template
    :param verbose_flag: This flag defines the output of intermediate information about the progress of the script.
    :return: 1) File "raw_parsed_output.txt" in script\'s directory
                2) List of dictionaries with data about devices

    I wanted to use CliTable, but I got error " ModuleNotFoundError: No module named 'fcntl' " when I tried to
        import clitable in Windows OS
    Again, I have to do everything myself :(
    """

    # Creating list of dictionaries with data about devices
    list_parsed_output_all_devices = []

    for device in list_of_command_output:
        dic_parsed_otpud_current_device = {}

        # We are setting compliance of device type -> command -> template
        device_type = device['device_type']

        # If we don't have output for the device we must to break iteration
        if device['output'] is None:
            if verbose_flag:
                print('\n', 'We don\'t have valid output for the device {} '.format(device['ip']))
            continue

        list_outpud_commands = list(device['output'].keys())

        for output_cmd in  list_outpud_commands:
            dic_parsed_otpud_current_cmd ={}

            template = dic_index[device_type][output_cmd]
            line_outpud = device['output'][output_cmd]

            if verbose_flag:
                print('\n',
                      'For device "{}" , which have the type: "{}" and output of command: "{}" we use template: "{}" '.format(device['ip'], device_type, output_cmd,  template))

            # We are opening the template file and parse the output.
            try:
                with open(template, 'r') as f_template:
                    re_table = textfsm.TextFSM(f_template)
                    header = re_table.header
                    result = re_table.ParseText(line_outpud)
                    dev_parsed_output = [header] + result

            except(FileNotFoundError):
                print('\n', 'Something went wrong. File of template not found or corrupted!')
                print('\n', 'We cannot parse output of command "{}" for device {} '.format(output_cmd, device['ip']))
                dev_parsed_output = None

            # Saving raw parsed output to file just in case
            with open('raw_parsed_output.txt', 'a') as raw_parsed_output:
                raw_parsed_output.writelines('\n')
                raw_parsed_output.writelines('Parsed output of command: "{}" for device "{}" '.format(output_cmd, device['ip']))
                raw_parsed_output.writelines('\n')
                raw_parsed_output.writelines(tabulate(dev_parsed_output, headers='firstrow',  tablefmt='grid'))
                raw_parsed_output.writelines('\n')

            # Сollecting parsed data to new dictionary. Commands level
            for i in range(len(dev_parsed_output[0])):
                try:
                    dic_parsed_otpud_current_cmd.update({dev_parsed_output[0][i]: dev_parsed_output[1][i]})
                except (IndexError):
                    # If there is no output (for example CDP was disabled), then adding an empty value.
                    dic_parsed_otpud_current_cmd.update({dev_parsed_output[0][i]: None})

            # Сollecting parsed data to new dictionary. Devices level
            dic_parsed_otpud_current_device.update({output_cmd: dic_parsed_otpud_current_cmd})

        # I choose and save only those parameters of device with I need at the time.
        # For different devices list of parameters are different
        if device['device_type'] is 'cisco_ios':
            list_parsed_output_all_devices.append({'ip': device['ip'],
                                                  'device_type': device['device_type'],
                                                  'hostname': device['hostname'],
                                                  'pid': dic_parsed_otpud_current_device['show inventory']['PID'],
                                                  'sn': dic_parsed_otpud_current_device['show inventory']['SN'],
                                                  'os': dic_parsed_otpud_current_device['show version']['VERSION'],
                                                  'uptime': dic_parsed_otpud_current_device['show version']['UPTIME']})

        elif device['device_type'] is 'cisco_wlc':
            list_parsed_output_all_devices.append({'ip': device['ip'],
                                                   'device_type': device['device_type'],
                                                   'hostname': dic_parsed_otpud_current_device['show sysinfo']['SYSTEM_NAME'],
                                                   'pid': dic_parsed_otpud_current_device['show inventory']['PID'],
                                                   'sn': dic_parsed_otpud_current_device['show inventory']['SN'],
                                                   'os': dic_parsed_otpud_current_device['show sysinfo']['PRODUCT_VERSION'],
                                                   'uptime': dic_parsed_otpud_current_device['show sysinfo']['SYSTEM_UP_TIME']})

        elif device['device_type'] is 'cisco_s300':
            list_parsed_output_all_devices.append({'ip': device['ip'],
                                                   'device_type': device['device_type'],
                                                   'hostname': device['hostname'],
                                                   'pid': dic_parsed_otpud_current_device['show inventory']['PID'],
                                                   'sn': dic_parsed_otpud_current_device['show inventory']['SN'],
                                                   'os': dic_parsed_otpud_current_device['show version']['VERSION'],
                                                   'uptime': None})

        else:
            print('\n', 'Something went wrong. Device type in parsed output wrong.')
            return (False, list_parsed_output_all_devices)


    print('\n', 'The raw parsed output was saved to file "raw_parsed_output.txt" in script\'s directory. Just in case.')
    return (True, list_parsed_output_all_devices)


def write_to_excel(list_parsed_devices_output, verbose_flag):
    """
    Function to write data to excel file
    :param list_parsed_devices_output: List of dictionaries with data about devices
    :param verbose_flag: This flag defines the output of intermediate information about the progress of the script.
    :return: File "inventory.xlsx" in script\'s directory
    """

    workbook = xlsxwriter.Workbook('inventory.xlsx')
    worksheet = workbook.add_worksheet()

    row = 0
    col = 0
    headers_writed = False

    # Retreat one column for line  position number
    worksheet.write(row, col, '#')
    col = 1

    try:
        for position, line in enumerate(list_parsed_devices_output):

            # First, write the headlines.
            if not headers_writed:
                for key in list(line.keys()):
                    worksheet.write(row, col, key)
                    headers_writed = True
                    col += 1

            # Writing of position
            row += 1
            col = 0
            worksheet.write(row, col, position + 1)
            col += 1

            # Writing of devices data
            for item in (list(line.values())):
                worksheet.write(row, col, item)
                col += 1

        workbook.close()
    except (PermissionError):
        print('\n', 'Script cannot get access to file "inventory.xlsx". '
                    'Close the file, check file system permission and try again.')
        return(False)


    return(True)




### Body

if __name__ == '__main__':

    # Flag of the correct execution of each step of the script
    all_doing_well = False

    # Timer of shoving error notification
    sleep_time = 5

    # Print welcome message and ask status of verbove_flag
    all_doing_well, verbose_flag = welcome_message()

    # Ask user to enter IP address list or range, and check the address type. We need Ip addresses private type only.
    if all_doing_well:
        all_doing_well, list_ipaddr_dev = request_ip_address_list(verbose_flag)
    else:
        print('\n', 'Something went wrong. Script execution will be terminated.')
        time.sleep(sleep_time)
        exit()

    # Checking availability IP's addresses of devices from list.
    if all_doing_well:
        all_doing_well, list_available_ipaddr_dev = check_ip_availability(list_ipaddr_dev, verbose_flag, limit=3)
    else:
        print('\n', 'Something went wrong. Run script again and double check your input')
        print('\n', 'Script execution will be terminated.')
        time.sleep(sleep_time)
        exit()

    # Ask user to enter username and password for devices
    if all_doing_well:
        all_doing_well, list_of_dic_devices = request_credentials(list_available_ipaddr_dev)
    else:
        print('\n', 'Something went wrong. We don\'t have any available IP. Run script again and double check your input')
        print('\n', 'Script execution will be terminated.')
        time.sleep(sleep_time)
        exit()

    # Connecting to device, sending commands and getting output
    if all_doing_well:
        # The commands are little different for different devices types
        dic_command = {'cisco_ios': ['show inventory', 'show version', 'show cdp neighbors'],
                        'cisco_wlc': ['show inventory', 'show sysinfo', 'show cdp neighbors detail'],
                        'cisco_s300': ['show inventory', 'show version', 'show cdp neighbors'],
                        }
        all_doing_well, list_command_output = send_command_and_get_output(list_of_dic_devices, dic_command, verbose_flag, limit=3)

    else:
        print('\n', 'Something went wrong. Run script again and double check your input')
        print('\n', 'Script execution will be terminated.')
        time.sleep(sleep_time)
        exit()

    # Parsing output of devices by TextFSM
    if all_doing_well:
        # We use different template for parsing of different output of different type of device
        dic_parse_index = {'cisco_ios': {'show cdp neighbors': 'templates/cisco_ios_show_cdp_neighbors.template',
                                         'show inventory': 'templates/cisco_ios_show_inventory.template',
                                         'show version': 'templates/cisco_ios_show_version.template'},
                           'cisco_s300': {'show cdp neighbors': 'templates/cisco_s300_ssh_show_cdp_neighbors.template',
                                          'show inventory': 'templates/cisco_s300_ssh_show_inventory.template',
                                          'show version': 'templates/cisco_s300_ssh_show_version.template'},
                           'cisco_wlc': {'show cdp neighbors detail': 'templates/cisco_wlc_ssh_show_cdp_neighbors_detail.template',
                                         'show inventory': 'templates/cisco_wlc_ssh_show_inventory.template',
                                         'show sysinfo': 'templates/cisco_wlc_ssh_show_sysinfo.template'}}

        all_doing_well, parsed_devices_output = parse_output_textfsm(list_command_output, dic_parse_index, verbose_flag)

    else:
        print('\n', 'Something went wrong. We didn\'t get any valid output from all devices')
        print('\n', 'Script execution will be terminated.')
        time.sleep(sleep_time)
        exit()

    # Writing got data to Excel file
    if all_doing_well:
        all_doing_well = write_to_excel(parsed_devices_output, verbose_flag)

    else:
        print('\n', 'Something went wrong. We didn\'t get any valid parsed data from all devices')
        print('\n', 'Script execution will be terminated.')
        time.sleep(sleep_time)
        exit()


    if all_doing_well:
        print('\n')
        print('\n')
        print('\n', '=' * 50)
        print('\n', 'The script completed successfully! :) ')
        print('\n', 'The data was saved to file "inventory.xlsx" into script\'s directory. ')
        print('\n')
        print('\n', '=' * 50)
        print('\n', 'Have a nice day! Bye!')
    else:
        print('\n', 'Something went wrong. The data was not saved to the Excel file, '
                    'but you can use the file "raw_parsed_output.txt" in script\'s directory.' )
