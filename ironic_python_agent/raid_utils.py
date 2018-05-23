

from oslo_config import cfg
from oslo_log import log as logging

from ironic_python_agent.hardware_managers import mega

LOG = logging.getLogger(__name__)
CONF = cfg.CONF

VALID_TYPE = ['front_end_computer', 'DB_computer_A', 'DB_computer_B']


def _get_config():
    configurations = {
        'front_end_computer': {
            'vendor': CONF.front_end_computer.vendor,
            'product': CONF.front_end_computer.product,
            'cpu_model': CONF.front_end_computer.cpu_model,
            'disk_num': CONF.front_end_computer.disk_num
        },
        'DB_computer_A': {
            'vendor': CONF.DB_computer_A.vendor,
            'product': CONF.DB_computer_A.product,
            'cpu_model': CONF.DB_computer_A.cpu_model,
            'disk_num': CONF.DB_computer_A.disk_num
        },
        'DB_computer_B': {
            'vendor': CONF.DB_computer_B.vendor,
            'product': CONF.DB_computer_B.product,
            'cpu_model': CONF.DB_computer_B.cpu_model,
            'disk_num': CONF.DB_computer_B.disk_num
        }
    }
    return configurations


def _normalize_cpu_model(raw_model):
     pos = raw_model.index('CPU')
     return raw_model[pos+4:pos+14]

def _parse_properties(properties):
    hw_info = {
        'vendor': properties.get('system_vendor').manufacturer,
        'product': properties.get('system_vendor').product_name,
        'cpu_model': _normalize_cpu_model(properties.get('cpu').model_name),
        'disk_num': len(properties.get('disks'))
    }
    return hw_info


def get_type_by_properties(properties):
    '''Get server's type by matching configurations and hardware properties.
       Like vendor/product/cpu_model/mem_size/disk_num/disk_size.
    '''
    configurations = _get_config()
    current_hw_info = _parse_properties(properties)

    for key, value in configurations.items():
        for item, val in value.items():
            if current_hw_info.get(item) != val:
                break
        else:
            return key

    return 'Unknown'


def config_raid(server_type):
    # Only support MegaRAID
    raid_manager = mega.MegaHardwareManager()
    raid_manager.config_raid_by_server_type(server_type)