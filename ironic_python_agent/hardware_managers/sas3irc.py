# Copyright (C) 2017 Inspur Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from oslo_config import cfg
from oslo_log import log
from ironic_python_agent import hardware
from ironic_python_agent import utils
from ironic_python_agent.hardware_managers.pmc import string_to_num
from math import fabs
import re

LOG = log.getLogger()
CONF = cfg.CONF

JBOD_ON = '1'
JBOD_OFF = '0'

MEGACLI = "sas3ircu"


def _detect_raid_card():
    cmd = "%s list" % MEGACLI
    try:
        utils.execute(cmd, shell=True)
        # return true if cmd succeeded
        return True
    except Exception:
        return False


def list_all_virtual_drives():
    """List all virtual drive Info

    The switches we use for Megacli: Virtual Drive Info for KEY="value" output, b for size output
    in bytes, d to exclude dependent devices (like md or dm devices), i to
    ensure ascii characters only, and o to specify the fields/columns we need.

    Broken out as its own function to facilitate custom hardware managers that
    don't need to subclass GenericHardwareManager.

    5:return: A list of Virtual Drive Info
    """
    report, _e = utils.execute(
        r"sas3ircu 0 display | grep -iE 'volume id|raid level|size \(in mb\)|phy.* enclosure|device is'",
        shell=True)
    lines = report.split('\n')

    pds = list_all_physical_devices()

    # enclosure:slot => physical drive
    enclosure_slot_dict = dict()
    for pd in pds:
        enclosure_slot_dict["%s:%s" % (pd['Enclosure_Device_Id'], pd['Slot_Id'])] = pd

    virtualdrives = []

    vd = dict()
    i = 0
    while i < len(lines):
        if lines[i].find('Volume ID') != -1:
            vd = dict()
            vd['Target_id'] = lines[i].split(':')[1].strip()
        elif lines[i].find('RAID level') != -1:
            vd['Raid_Level'] = lines[i].split(':')[1].strip()
        elif lines[i].find('Size') != -1:
            if len(vd.keys()) == 0:
                break
            vd['Size'] = lines[i].split(':')[1].strip() + "MB"
        elif lines[i].find('PHY') != -1:
            num = 0
            enclosure_slot_pair = []
            vd['drives'] = enclosure_slot_pair
            while i < len(lines) and lines[i].find('PHY') != -1:
                num += 1
                key = lines[i].split(':', 1)[1].strip()
                physical_drive = enclosure_slot_dict[key]
                enclosure_slot_pair.append(physical_drive)
                i += 1

            vd['Drive_Num'] = num
            virtualdrives.append(vd)
            vd = dict()
        else:
            if len(vd.keys()) > 0:
                LOG.warning('UNKNOWN FIELD FOUND!!! cmd result is %s', report)
            break
        i += 1

    LOG.info('The Virtual Drive Info:[%s]', virtualdrives)

    return virtualdrives


def list_all_physical_devices():
    """List all physical disk devices

    The switches we use for Megacli: Physical for KEY="value" output, b for size output
    in bytes, d to exclude dependent devices (like md or dm devices), i to
    ensure ascii characters only, and o to specify the fields/columns we need.

    Broken out as its own function to facilitate custom hardware managers that
    don't need to subclass GenericHardwareManager.

    :param block_type: Type of Physical Drive to find
    :return: A list of Physical Drive
    """

    report, _e = utils.execute(
        r"sas3ircu 0 display | grep -iE '^\s+enclosure|^\s+slot|size \(in MB\)|protocol|Drive Type'",
        shell=True)
    lines = report.split('\n')

    # fixme ungly code
    if lines[1].find('Size') > 0:
        lines = lines[1:]

    LOG.debug("list all physical devices return %s", "\n".join(lines))
    lines = lines[:-1]
    while lines[-1].find('Enclosure') != -1:
        lines = lines[:-1]

    i = 1
    j = 0
    devices = []
    adapter = 0
    adaptercount = 0
    LOG.info('Get line string is: %s', lines)
    while i < len(lines):
        # Split into KEY=VAL pairs
        if lines[i].find('Adapter') != -1:
            adapter = lines[i].split('#')[1]
            i += 1
            LOG.info('Get a Adapter with id: %s. Continuing', adapter)
        elif lines[i].find('Device is a Enclosure') != -1:
            break
        elif lines[i].find('Adapter') == -1:
            device = {}
            # 5 metrics are collected
            # Enclosure ID, slot number, Raw size
            for j in range(i, len(lines)):
                LOG.info('Parse the Megacli Result for Physical Disk: %s', lines[j])
                if lines[j].find("Adapter") != -1:
                    adapter = lines[i].split('#')[1]
                    i = j + 1
                elif lines[j].find('Device is a Enclosure') != -1:
                    # a workaround
                    break
                elif lines[j] == "":
                    i = j + 1
                    break
                elif lines[j].find("Adapter") == -1:
                    device['Adapter_id'] = adapter
                    # increment i by 1 avoid endless looping
                    i = j + 1
                    # Enclosure & Slot are required when adding configurations
                    if j % 5 == 1:
                        device['Enclosure_Device_Id'] = lines[j].split(':')[1].strip()
                    if j % 5 == 2:
                        device['Slot_Id'] = lines[j].split(':')[1].strip()
                    if j % 5 == 4:
                        # Physical Disk Type
                        device['Type'] = lines[j].split(':')[1].strip()
                    if j % 5 == 3:
                        disk_size = lines[j].split(':')[1]
                        disk_size = disk_size.split('/')
                        disk_size = disk_size[0].strip() + "MB"

                        # LSI Raw type is same as PMC total size
                        device['Total Size'] = disk_size
                    if j % 5 == 0:
                        # Inquiry Data: Manufacturer & Series Number
                        device['Model'] = lines[j].split(':')[1].strip()
                        copy = device.copy()
                        if re.search(r'SSD|Micron_5200', copy['Model']) is not None:
                            copy['Type'] = 'SSD'
                        devices.append(copy)

    return devices


class SAS3IRCManager(hardware.GenericHardwareManager):
    HARDWARE_MANAGER_NAME = 'SAS3IRCManager'
    HARDWARE_MANAGER_VERSION = '1.0'

    def evaluate_hardware_support(self):
        if _detect_raid_card():
            LOG.debug('Found SAS3 Raid card')
            return hardware.HardwareSupport.MAINLINE
        else:
            LOG.debug('No LSI SAS3 card found')
            return hardware.HardwareSupport.NONE

    def get_clean_steps(self, node, ports):
        return [
            {
                'step': 'create_configuration',
                'priority': 0,
                'interface': 'raid',
            },
            {
                'step': 'delete_configuration',
                'priority': 0,
                'interface': 'raid',
            }
        ]

    def create_configuration(self, node, ports):

        target_raid_config = node.get('target_raid_config', {}).copy()
        target_raid_config_list = target_raid_config['logical_disks']

        LOG.info('Begin to create configuration')
        for vdriver in target_raid_config_list:
            size = None
            raid_level = None
            physical_disks = None
            controller = None
            is_root_volume = None

            if vdriver.has_key('size_gb'):
                size = vdriver['size_gb']
            if vdriver.has_key('raid_level'):
                raid_level = vdriver['raid_level']
            if vdriver.has_key('physical_disks'):
                physical_disks = vdriver['physical_disks']
            if vdriver.has_key('controller'):
                controller = vdriver['controller']
            if vdriver.has_key('is_root_volume'):
                is_root_volume = vdriver['is_root_volume']
            LOG.info('Raid Configuration:[size:%s, raid_level:%s, p_disks:%s, controller:%s]',
                     size, raid_level, physical_disks, controller)
            disklist = " "
            for i in range(0, len(physical_disks)):
                if i == 0:
                    disklist = physical_disks[i]
                else:
                    disklist = disklist + "," + physical_disks[i]

            LOG.info('Raid disk list:[%s]', disklist)
            if raid_level is not None and physical_disks is not None and controller is not None:
                cmd = ('%s -CfgLdAdd ' % MEGACLI) + '-r' \
                      + raid_level + "[" + disklist + "] " + "-a" + controller

                LOG.info('Raid Configuration Command:%s', cmd)
                report, _e = utils.execute(cmd, shell=True)
            else:
                LOG.info('Param Error,No Raid Configuration Command being Created:%s', cmd)

        return target_raid_config

    def delete_configuration(self):

        LOG.info('Begin to delete configuration')
        cmd = '%s 0 delete noprompt' % MEGACLI
        utils.execute(cmd, shell=True)

    def _check_before_config(self, physical_disks):
        adp_list = []
        enclosure_list = []
        for pd in physical_disks:
            adp_list.append(pd.adapter_id)
            enclosure_list.append(pd.enclosure_id)

        if len(set(adp_list)) != 1 or len(set(enclosure_list)) != 1:
            return False

        return True

    def config_raid_by_server_type(self, type):
        physical_disks = list_all_physical_devices()
        if not self._check_before_config(physical_disks):
            LOG.error("Can not configure RAID cause of not consistent adaptor or enclosure!")
            return

        pd_list = ""
        for pd in physical_disks:
            enid_pdid = str(pd.enclosure_id) + ":" + str(pd.slot_id)
            pd_list = pd_list + "," + enid_pdid

        if type == 'front_end_computer':
            raid_level = CONF.front_end_computer.raid_level
        elif type == 'DB_computer_A':
            raid_level = CONF.DB_computer_A.raid_level
        else:
            raid_level = CONF.DB_computer_B.raid_level

        cmd = ('%s -CfgLdAdd ' % MEGACLI) + '-r' \
              + str(raid_level) + "[" + pd_list + "] " + "-a" + physical_disks[0].adapter_id
        utils.execute(cmd)

    @staticmethod
    def group_physical_drives_by_type(physical_drives):

        group = {
            "SSD": [],
            "SAS": [],
            "SATA": []
        }
        for drive in physical_drives:
            if group.get(drive['Type']) is None:
                group[drive['Type']] = []
            group.get(drive['Type']).append(drive.copy())
        return group

    @staticmethod
    def generate_logical_drive_configuration(physical_drives):

        group = SAS3IRCManager.group_physical_drives_by_type(physical_drives)
        ssd, sas, sata = group['SSD'], group['SAS'], group['SATA']
        configuration = {}
        if len(physical_drives) == 2:
            configuration['task1'] = {
                # both PDs will have same size
                "size": physical_drives[0]['Total Size'],
                "level": "1",
                "num": 2,
                "type": "SAS"
            }
        elif len(ssd) == 0:
            # there is no SSD
            if len(sas) == 2 and len(sata) == 8:
                configuration['task1'] = {
                    "size": sas[0]['Total Size'],
                    "level": "1",
                    "num": 2,
                    "type": "SAS"
                }
                configuration['task2'] = {
                    "size": sata[0]['Total Size'],
                    "level": "5",
                    "num": 8,
                    "type": "SATA"
                }
            elif len(sas) == 2:
                configuration['task1'] = {
                    "size": sas[0]['Total Size'],
                    "level": "1",
                    "num": 2,
                    "type": "SAS"
                }
        elif len(ssd) == 4 and len(sas) == 2 and len(sata) == 0:
            configuration['task1'] = {
                "size": sas[0]['Total Size'],
                "level": "1",
                "num": 2,
                "type": "SAS"
            }
            configuration['task2'] = {
                "size": ssd[0]['Total Size'],
                "level": "5",
                "num": 4,
                "type": "SSD"
            }
        elif len(ssd) == 2 and len(sas) == 2 and len(sata) == 10:
            configuration['task1'] = {
                "size": sas[0]['Total Size'],
                "level": "1",
                "num": 2,
                "type": "SAS"
            }
        elif len(ssd) == 10 and len(sas) == 2:
            configuration['task1'] = {
                "size": sas[0]['Total Size'],
                "level": "1",
                "num": 2,
                "type": "SAS"
            }
            configuration['task2'] = {
                "size": ssd[0]['Total Size'],
                "level": "5",
                "num": 10,
                "type": "SSD"
            }

            # if string_to_num(ssd[0]['Total Size']) > 700 * 1024:
            #     configuration['task3'] = {
            #         "size": sata[0]['Total Size'],
            #         "level": "5",
            #         "num": len(sata),
            #         "type": "SATA"
            #     }
        elif len(ssd) == 8:
            pass
        elif len(ssd) == 4:
            configuration['task1'] = {
                "size": ssd[0]['Total Size'],
                "level": "1",
                "num": 2,
                "type": "SSD"
            }
            # configuration['task2'] = {
            #    "size": ssd[0]['Total Size'],
            #    "level": "1",
            #    "num": 2,
            #    "type": "SSD"
            # }
        return configuration

    def configure_node(self):
        """
        configure
        :return:  raid_profile : a dict whose key is raid level and values are
                                 corresponding physical drives
        """
        try:
            # delete existing configurations
            self.delete_configuration()

            # list all existing physcial disks
            physical_disks = list_all_physical_devices()
            LOG.debug("all existing physical devices: %s", physical_disks)

            # generate configuration profile
            configs = self.generate_logical_drive_configuration(physical_disks)

            # add configuration in accordance to profile
            for task_key in sorted(configs.keys()):

                # fetch one configuration
                task_config = configs[task_key]

                size = task_config['size']  # physical drive raw size
                level = task_config['level']  # raid level
                num = task_config['num']  # number of disks
                disk_type = task_config['type']  # disk type ssd, sas, sata

                # select raid candidates
                candidates = sorted([(i, val) for i, val in enumerate(physical_disks)
                                     if not disk_type or val.get('Type') == disk_type],
                                    key=lambda x: fabs(string_to_num(x[1]['Total Size']) - string_to_num(size)))

                # select the first num feasible candidates
                candidates = candidates[0:num]

                # delete selected pds from candidate list
                # To avoid reindexing, delete backwads
                for i, _ in sorted(candidates, key=lambda x: -x[0]):
                    del physical_disks[i]

                # prepare configuration strings
                enclosure_device_list = ["%s:%s" % (val['Enclosure_Device_Id'], val['Slot_Id']) for i, val in
                                         candidates]
                cmd = ('%s 0 create ' % MEGACLI) + ' raid' \
                      + str(level) + " max " + ' '.join(enclosure_device_list) + " noprompt"

                LOG.debug("create virtual volume with %s", cmd)
                utils.execute(cmd, shell=True)

        except Exception as e:
            LOG.info('raid configuration failed, %s' % e)

        # list all existing logical drives
        physical_disks = list_all_physical_devices()
        physical_disk_dict = {}
        for pd in physical_disks:
            key = ":".join([pd['Enclosure_Device_Id'], pd['Slot_Id']])
            physical_disk_dict[key] = pd

        logical_drives = list_all_virtual_drives()
        raid_profile = {}
        for logical_drive in logical_drives:
            if raid_profile.get(logical_drive['Raid_Level']) is None:
                raid_profile[logical_drive['Raid_Level']] = []
            for drive in logical_drive['drives']:
                del physical_disk_dict[":".join([drive['Enclosure_Device_Id'], drive['Slot_Id']])]
            raid_profile[logical_drive['Raid_Level']].append(logical_drive['drives'])

        for key, val in physical_disk_dict.items():
            if raid_profile.get('RAW') is None:
                raid_profile['RAW'] = []
            raid_profile['RAW'].append({
                'Total Size': val['Total Size'],
                'Type': val['Type'],
                'Model': val['Model']
            })
        return raid_profile
