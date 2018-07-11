import subprocess
import re
from math import fabs
from oslo_log import log
from ironic_python_agent import hardware

LOG = log.getLogger()

def string_to_num(num_string):

    try:
        return int(num_string)
    except Exception:
        pass

    index=0
    for i in num_string:
        if not i.isdigit():
            break
        index = index + 1
    val = int(num_string[0:index])
    unit = num_string[index:].strip()

    if unit.lower() == 'gb':
        return val * 1024
    elif unit.lower() == 'tb':
        return val * 1024 * 1024
    return val


def run_command(cmd=None):
    if cmd is None:
        return '', -1
    return subprocess.Popen(cmd, shell=True, stderr=subprocess.STDOUT, stdout=subprocess.PIPE).communicate()


class WorkerBase(object):

    ctl_num = 1    # number of controllers(adapter)
    config = None  # configuration
    controllers = []  # list of raid controllers to which physical drives attached

    def __init__(self, config):
        self.config = config

    def generate_pd_profile(self, run_command=run_command):
        pass

    def __resolve_config(self, config=None):
        pass

    def clear_previous_configs(self, run_command=run_command):
        pass

    def add_new_configs(self, config=None, run_command=run_command):
        """
        configure new logical drives, all the left out drives will be configured
        to raid 0
        :param config: raid configuration
        :param run_command: function used to execute commands
        """
        pass

    def init_configs(self, run_command=run_command):
        pass

    def config_node(self, run_command=None):
        pass


class PmcWorker(WorkerBase):
    """
    configure PMC controller
    """

    def __init__(self, config=None):
        self.config = config
        self.lds = []
        self.__get_controller_num(run_command=lambda x: (1, 0))

    @staticmethod
    def get_pd_type(pd):
        if pd['SSD'].lower() == 'yes':
            return "SSD"
        else:
            return pd['Transfer Speed'].split(' ')[0]

    def generate_pd_profile(self, run_command=run_command):
        """
        :param run_command:
        :return:
        """

        # ignore last line
        cmd_template = "arcconf getconfig %s PD | sed -ne '5,$p' | head -n -1"
        regexp = r'\s*Device #.*'
        self.controllers = []
        is_hdd = True
        for i in range(self.ctl_num):
            cmd = cmd_template % (i + 1)
            result, _ = run_command(cmd)
            result = result.strip()
            pd = {}
            pds = []
            for line in result.split('\n'):
                if re.match(r'^\s*$', line):
                    continue

                if re.match(regexp, line):
                    is_hdd = True
                    if len(pd.keys()) > 0:
                        pd['Type'] = self.get_pd_type(pd)
                        pds.append(pd)
                        pd = {}
                    continue

                if not is_hdd:
                    '''continue if not hard drive device'''
                    continue

                try:
                    key, value = line.split(' : ')
                    pd[key.strip()] = value.strip()
                except Exception:
                    '''assume that there are no other situations besides xxx is xxx device'''
                    if 'Device is a Hard drive' not in line:
                        is_hdd = False
                    pass

            if is_hdd:
                '''append this PD only if it is hard drive device'''
                pd['Type'] = self.get_pd_type(pd)
                pds.append(pd)
            self.controllers.append(pds)
        return self.controllers

    def __get_controller_num(self, run_command=run_command):
        cmd = "arcconf getconfig 1 | head -1 | cut -d':' -f2 "
        result, status = run_command(cmd)
        self.ctl_num = int(result)
        return result

    def clear_previous_configs(self, run_command=run_command):
        """
        delete logical drives (LD)
        :return:
        """
        cmd = "arcconf delete %s logicaldrive all noprompt"
        for i in range(self.ctl_num):
            new_cmd = cmd % (i + 1)
            run_command(new_cmd)

        # uninitialize all pds
        cmd_uninit = "arcconf uninit %s all noprompt"
        for i in range(self.ctl_num):
            run_command(cmd_uninit % (i + 1))

    def classify_pd(self):
        """
        classify physical drives into groups of SSD, SAS & SATA
        :return:
        """

        pds = self.controllers[0]

        ssd, sas, sata = [], [], []

        for pd in pds:
            if pd['SSD'].lower() == 'yes':
                ssd.append(pd.copy())
            elif 'sas' in pd['Transfer Speed'].lower():
                sas.append(pd.copy())
            else:
                sata.append(pd.copy())

        return {
            'ssd': ssd,
            'sas': sas,
            'sata': sata
        }

    def gen_config(self):
        """
        generate configuration automatically
        enumerate configuration by server's physical profile
        :return: configuration
        """
        ctl_num = self.ctl_num
        pds = self.controllers[0]
        classified_pds = self.classify_pd()
        ssd, sas, sata = classified_pds['ssd'], classified_pds['sas'], classified_pds['sata']
        configuration = {}

        if len(pds) == 2:
            configuration['task1'] = {
                '''both PDs will have same size'''
                "size": pds[0]['Total Size'],
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

    def config_node(self, run_command=run_command):
        """
        uninitialized pds will be kept untouched
        their state is 'ready'
        :param run_command:
        :return:
        """

        if self.config is None:
            self.config = self.gen_config()

        def get_channel_device_pair(str_input):
            return tuple(str_input.split('(')[0].split(','))

        cmd_template = "arcconf create %s logicaldrive Mehtod QUICK Rcache RON Wcache WBB MAX %s %s noprompt"
        for key in sorted(self.config.keys()):
            config = self.config[key]
            size = string_to_num(config.get('size'))
            disk_type = None
            level = config.get('level')
            num = config.get('num')

            # sorting by size and disk-type
            # get the first *num* feasible disks
            # it works fine assuming different disk model will have different disk sizes
            candidates = sorted([(i, val) for i, val in enumerate(self.controllers[0])
                                 if not disk_type or val.get('Type') == disk_type],
                                key=lambda x: fabs(string_to_num(x[1]['Total Size']) - size))
            candidates = candidates[0:num]

            # delete selected pds from candidate list
            for i, _ in sorted(candidates, key=lambda x: -x[0]):
                del self.controllers[0][i]
            enclosure_device_list = ["%s %s" % get_channel_device_pair(val['Reported Channel,Device(T:L)'])
                                     for i, val in candidates]
            self.__initialize_disk(enclosure_device_list, run_command)
            cmd = cmd_template % (1, level, " ".join(enclosure_device_list))
            run_command(cmd)

    @staticmethod
    def __initialize_disk(enclosure_device_list, run_command=run_command):
        cmd = "arcconf task start 1 device %s initialize noprompt"
        for device in enclosure_device_list:
            run_command(cmd % device)

    def get_raid_config(self):
        """
        generate raid configuration dict
        run self.generate_pd_profile and self.get_ld_profile before this method

        assume there is only one controller
        :return: a dict containing raid configuration
        """
        raid_config = {}

        pd_detail = {}

        for pd in self.controllers[0]:
            pd_detail[pd['Serial number']] = pd

        for ld in self.lds:
            level = "RAID %s" % ld['RAID level']
            if raid_config.get(level) is None:
                raid_config[level] = []
            raid_config[level].append([{
                'Type': pd_detail[i]['Type'],
                'Model': '%s %s' % (pd_detail[i]['Vendor'], pd_detail[i]['Model']),
                'Total Size': pd_detail[i]['Total Size']
            } for i in ld['pd']])

        for _, pd in pd_detail.items():
            if 'Raw' in pd.get('State'):
                if raid_config.get('RAW') is None:
                    raid_config['RAW'] = []
                #raid_config['Raw'].append(pd['Serial number'])
                raid_config['RAW'].append({
                    'Model': '%s %s' % (pd['Vendor'], pd['Model']),
                    'Total Size': pd['Total Size'],
                    'Type': pd['Type']
                })
        return raid_config

    def get_ld_profile(self, run_command=run_command):
        """
        generate logical drive profile
        :param run_command:
        :return:
        """
        cmd = "arcconf getconfig 1 ld | sed -ne '5,$p' | head -n -1"
        res, _ = run_command(cmd)
        self.lds = []
        ld = {}
        for line in res.split('\n'):
            if re.match(r'^\s*$', line):
                continue

            if 'Logical device number' in line:
                if len(ld.keys()) != 0:
                    self.lds.append(ld)
                    ld = {}
                continue

            if ':' not in line:
                continue

            try:
                name, value = line.split(' : ')

                if 'Segment' in name:
                    if ld.get('pd') is None:
                        ld['pd'] = []
                    ld['pd'].append(value.split(' ')[-1])
                else:
                    ld[name.strip()] = value.strip()
            except Exception:
                pass

        if len(ld.keys()) > 0:
            self.lds.append(ld)

        return self.lds


class PmcHardwareManager(hardware.GenericHardwareManager):
    HARDWARE_MANAGER_NAME = 'PMC'
    HARDWARE_MANAGER_VERSION = "1.0"

    def evaluate_hardware_support(self):
        cmd = "lspci | grep -i 'adaptec'"
        try:
            result, _ = run_command(cmd)
            if result:
                return hardware.HardwareSupport.SERVICE_PROVIDER
            else:
                return hardware.HardwareSupport.NONE
        except Exception:
            return hardware.HardwareSupport.NONE

    def configure_node(self):

        # configure raid
        try:
            pmc = PmcWorker()
            pmc.clear_previous_configs()
            pmc.generate_pd_profile()
            pmc.config_node()
        except Exception as e:
            log.INFO('RAID configuration failed %s' % e)

        # dump raid configuration
        # {
        #    "RAID 1" : [],
        #    "RAID 5" : [],
        #    "RAW"    : []
        # }
        #
        pmc = PmcWorker()
        pmc.generate_pd_profile()
        pmc.get_ld_profile()
        return pmc.get_raid_config()
