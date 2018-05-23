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

LOG = log.getLogger()
CONF = cfg.CONF

def _detect_raid_card():
    cmd = "/opt/MegaRAID/MegaCli/MegaCli64 -adpCount | grep Controller"
    report, _e = utils.execute(cmd, shell=True)
    clounms = report.split(':')
    LOG.debug('Get Adapter Info:%s', clounms[1])
    adaptercount = int(clounms[1].split('.')[0])
    if adaptercount == 0:
        return False
    else:
        return True

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
                cmd = '/opt/MegaRAID/MegaCli/MegaCli64 -CfgLdAdd ' + '-r' \
                    + raid_level + "[" + disklist + "] " + "-a" + controller

                LOG.info('Raid Configuration Command:%s', cmd)
                report, _e = utils.execute(cmd, shell=True)
            else:
                LOG.info('Param Error,No Raid Configuration Command being Created:%s', cmd)

        return target_raid_config

    def delete_configuration(self, node, ports):

       LOG.info('Begin to delete configuration')
       cmd = '/opt/MegaRAID/MegaCli/MegaCli64 -CfgLdDel -LAll -a0'
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

        cmd = '/opt/MegaRAID/MegaCli/MegaCli64 -CfgLdAdd ' + '-r' \
              + str(raid_level) + "[" + pd_list + "] " + "-a" + physical_disks[0].adapter_id
        utils.execute(cmd)


