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

from ironic_python_agent import utils
from ironic_python_agent import hardware
from ironic_python_agent.hardware_managers.pmc import string_to_num
from math import fabs

LOG = log.getLogger()
CONF = cfg.CONF

JBOD_ON = '1'
JBOD_OFF = '0'

MEGACLI="/opt/MegaRAID/MegaCli/MegaCli64"

def _detect_raid_card():
    cmd = "%s -adpCount | grep Controller" % MEGACLI
    try:
        report, _e = utils.execute(cmd, shell=True)
        clounms = report.split(':')
        LOG.debug('Get Adapter Info:%s', clounms[1])
        adaptercount = int(clounms[1].split('.')[0])
        if adaptercount == 0:
            return False
        else:
            return True
    except Exception:
        return False

class MegaHardwareManager(hardware.GenericHardwareManager):
    HARDWARE_MANAGER_NAME = 'MegaHardwareManager'
    HARDWARE_MANAGER_VERSION = '1.0'

    def evaluate_hardware_support(self):
        if _detect_raid_card():
            LOG.debug('Found LSI Raid card')
            return hardware.HardwareSupport.MAINLINE
        else:
            LOG.debug('No LSI Raid card found')
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
            for i in range(0,len(physical_disks)):
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

    def delete_configuration(self, node, ports):

       LOG.info('Begin to delete configuration')
       cmd = '%s -CfgLdDel -LAll -a0' % MEGACLI
       report, _e = utils.execute(cmd, shell=True)
       target_raid_config = node.get('target_raid_config', {}).copy()
       return target_raid_config

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
        physical_disks = hardware.list_all_physical_devices()
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

        group = MegaHardwareManager.group_physical_drives_by_type(physical_drives)
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
            '''there is no SSD'''
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
            configuration['task2'] = {
                "size": ssd[0]['Total Size'],
                "level": "1",
                "num": 2,
                "type": "SSD"
            }
        return configuration

    @staticmethod
    def set_jbod_mode(self, mode):
        """
        By default, LSI does not support JBOD, so enabling JBOD mode is mandatory
        Assume single RAID card
        :return:
        """
        cmd = "%s -AdpGetProp EnableJBOD -a0 | grep -c \"Disabled\"" % MEGACLI
        result, _ = utils.execute(cmd, shell=True)

        # reconfigure mode only if mode conflict
        if mode == JBOD_ON and int(result) == 0 \
                or mode == JBOD_OFF and int(result) == 1:
            return

        # enabling JBOD may require a reboot
        cmd = "%s -AdpSetProp EnableJBOD %s -a0" % (MEGACLI, mode)
        utils.execute(cmd, shell=True)

    def configure_node(self):
        """
        configure
        :return:  raid_profile : a dict whose key is raid level and values are
                                 corresponding physical drives
        """
        try:
            # turn off jbod
            self.set_jbod_mode(JBOD_OFF)

            # delete existing configurations
            self.delete_configuration()

            # list all existing physcial disks
            physical_disks = hardware.list_all_physical_devices()
            LOG.debug("all existing physical devices: %s", physical_disks)

            # generate configuration profile
            configs = self.generate_logical_drive_configuration(physical_disks)



            # add configuration in accordance to profile
            for task_key in sorted(configs.keys()):

                # fetch one configuration
                task_config = configs[task_key]

                size = task_config['size']          # physical drive raw size
                level = task_config['level']        # raid level
                num = task_config['num']            # number of disks
                disk_type = task_config['type']     # disk type ssd, sas, sata

                # select raid candidates
                candidates = sorted([(i, val) for i, val in enumerate(physical_disks)
                                     if not disk_type or val.get('Type') == disk_type],
                                    key=lambda x: fabs(string_to_num(x[1]['Total Size']) - size))

                # select the first num feasible candidates
                candidates = candidates[0:num]

                # delete selected pds from candidate list
                # To avoid reindexing, delete backwads
                for i, _ in sorted(candidates, key=lambda x: -x[0]):
                    del physical_disks[i]

                # prepare configuration strings
                enclosure_device_list = ["%s:%s" % (val['Enclosure_Device_Id'], val['Slot_Id']) for i, val in candidates]
                cmd = ('%s -CfgLdAdd ' % MEGACLI) + '-r' \
                      + str(level) + "[" + ','.join(enclosure_device_list) + "] WB RA Cached " + "-a" + '0'
                utils.execute(cmd)

            # if there are unconfigured disks available
            # this implies that pass through mode is required
            # then enable JBOD mode
            if len(physical_disks) > sum([len(val) for key, val in configs.items()]):
                LOG.debug('enable JBOD mode')
                self.set_jbod_mode(JBOD_ON)
        except Exception as e:
            LOG.INFO('raid configuration failed, %s' % e)

        # list all existing logical drives
        physical_disks = hardware.list_all_physical_devices()
        physical_disk_dict = {}
        for pd in physical_disks:
            physical_disk_dict[pd['Model']] = pd

        logical_drives = hardware.list_all_virtual_drives()
        raid_profile = {}
        for logical_drive in logical_drives:
            if raid_profile.get(logical_drive['Raid_Level']) is None:
                raid_profile[logical_drive['Raid_Level']] = []
            for drive in logical_drives:
                del physical_disk_dict[drive['Model']]
            raid_profile[logical_drive['Raid_Level']].append(logical_drive.drivers)

        for key, val in physical_disk_dict.items():
            if raid_profile.get('RAW') is None:
                raid_profile['RAW'] = []
            raid_profile['RAW'].append({
                'Total Size': val['Total Size'],
                'Type': val['Type'],
                'Model': val['Model']
            })
        return raid_profile



