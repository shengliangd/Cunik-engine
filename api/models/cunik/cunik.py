"""class Cunik."""

from api.models.image_registry import image_registry
from api.models.data_volume_registry import data_volume_registry
from api.models.cunik_registry import cunik_registry
from backend.vm import VM, VMConfig
from os import path
from config import cunik_root
import uuid
import json
import sys
import os


class CunikConfig:
    """Config of a cunik, constructed when the user wants to create a Cunik."""

    def __init__(self, **kwargs):
        vital_keys_set = {'name', 'image', 'cmdline', 'hypervisor', 'memory'}
        all_keys_set = set.union(vital_keys_set, {'nic', 'data_volume'})
        if not set(kwargs.keys()) <= all_keys_set:
            raise KeyError('[ERROR] ' + list(set(kwargs.keys()) - all_keys_set)[0] +
                           ' is an invalid keyword argument for this function')
        if not set(kwargs.keys()) >= vital_keys_set:
            raise KeyError('[ERROR] ' + list(vital_keys_set - set(kwargs.keys()))[0] +
                           ' is a vital keyword argument for this function but has not been set')
        # name of Cunik instance
        self.name = kwargs.get('name')
        # path to image file
        try:
            self.image = image_registry.get_image_path(kwargs.get('image'))
        except KeyError as KE:
            sys.stderr.write('[ERROR] cannot find image {} in registry\n'.format(kwargs['image']))
            raise KE
        # command line parameters
        self.cmdline = kwargs.get('cmdline')
        # VM type
        self.hypervisor = kwargs.get('hypervisor')
        self.nic = kwargs.get('nic')
        # memory size in KB
        try:
            self.memory = int(kwargs['memory'])
        except ValueError as VE:
            sys.stderr.write('[ERROR] memory size must be an integer\n')
            raise VE
        try:
            assert self.memory > 0
        except AssertionError as AE:
            sys.stderr.write('[ERROR] memory size must be a positive integer\n')
            raise AE
        # data volume name
        if kwargs.get('data_volume'):
            try:
                self.data_volume = data_volume_registry.get_volume_path(kwargs['data_volume'])
            except KeyError as KE:
                sys.stderr.write('[ERROR] cannot find data volume {} in registry\n'.format(kwargs['data_volume']))
                raise KE

    @staticmethod
    def fill(path_to_cmdline: str, path_to_params: str, **kwargs):
        try:
            with open(path.join(cunik_root, path_to_cmdline)) as f:
                cmdline = f.read()
        except IOError as IE:
            sys.stderr.write('[ERROR] cmdline file not found: {0}\n'.format(IE))
            raise IE
        try:
            with open(path.join(cunik_root, path_to_params)) as f:
                params = json.loads(f.read())
        except ValueError as VE:
            sys.stderr.write('[ERROR] {0} is not a valid json file: {1}\n'.format(path_to_params, VE))
            raise VE
        except IOError as IE:
            sys.stderr.write('[ERROR] params file not found: {0}\n'.format(IE))
            raise IE
        params.update(kwargs)
        list_of_cmdline = cmdline.split('"')
        try:
            list_of_cmdline = [params[p[2:-2]] if p[:2] == '{{' and p[-2:] == '}}' else p for p in list_of_cmdline]
        except KeyError as KE:
            sys.stderr.write('[ERROR] params in cmdline not filled: {0}\n'.format(KE))
            raise KE
        return '"'.join(list_of_cmdline)


class Cunik:
    """Represent a cunik.

    All the public methods of this class will immediately
    affect cunik registry and virtual machine unless it raises an exception.

    Usage:
        >>> cu = Cunik(...)  # Now there is a new cunik in cunik registry along with the vm instance
        >>> cu.start()  # Now it starts, and the new status is updated in cunik registry
        >>> cu.stop()
        >>> del cu  # NOTICE: This really destroys corresponding vm and remove this cunik from registry
    """

    def __init__(self, config: CunikConfig):
        """Initialize the cunik"""
        # Create the vm with the image
        self.id = uuid.uuid4()
        self.state = 'Not started'
        vmc = VMConfig()
        vmc.name = config.name
        vmc.image_path = config.image
        vmc.cmdline = config.cmdline
        vmc.vdisk_path = config.data_volume
        vmc.hypervisor = config.hypervisor
        vmc.nic = config.nic
        vmc.memory_size = int(config.memory)
        self.vm = VM(vmc)
        # Register the cunik in the registry
        cunik_registry.register(self)

    def start(self):
        """Start the cunik."""
        # Start the vm
        self.vm.start()
        self.state = 'Running'
        # Update in registry
        cunik_registry.populate(self)

    def stop(self):
        """Stop the cunik."""
        # Stop the vm
        self.vm.stop()
        self.state = 'Stopped'
        # Update in registry
        cunik_registry.populate(self)

    def destroy(self):
        """Destroy a cunik according to the config."""
        # Destroy the vm
        del self.vm
        # Remove from registry
        cunik_registry.remove(self)


class CunikApi:
    counter = dict()

    @staticmethod
    def create(image_name, params=None, **kwargs):
        """
        Create a new cunik.

        Usage:
            >>> cunik = CunikApi.create('nginx', {'ipv4_addr': '10.0.20.1'})
            >>> print(cunik["id"])
        """

        def trans(ipv4):
            list_of_ipv4 = ipv4.split('.')
            list_of_ipv4[-1] = '100'
            return '.'.join(list_of_ipv4)

        if not params:
            params = {}
        with open(path.join(cunik_root, 'images', image_name, 'config.json')) as f:
            default_config = json.load(f)
        if default_config.get('data_volume'):
            data_volume_registry.add_volume_path(image_name,
                                                 '../images/{}/{}'.format(image_name, default_config['data_volume']))
            default_config['data_volume'] = image_name
        if not CunikApi.counter.get(image_name):
            CunikApi.counter[image_name] = 0
        CunikApi.counter[image_name] += 1
        tap_device_name = 'tap-{}-{}'.format(image_name, CunikApi.counter[image_name])
        if params.get('ipv4_addr'):
            os.system('ip l del {} 2>/dev/null'.format(tap_device_name))
            os.system('ip tuntap add {} mode tap'.format(tap_device_name))
            os.system('ip addr add {}/24 dev {}'.format(trans(params['ipv4_addr']), tap_device_name))
            os.system('ip link set dev {} up'.format(tap_device_name))
        cfg = CunikConfig(
            name=image_name + str(CunikApi.counter[image_name]),
            image=image_name,
            cmdline=CunikConfig.fill('images/{}/cmdline'.format(image_name), 'images/{}/params.json'.format(image_name),
                                     **params),
            nic=tap_device_name,
            **default_config
        )
        Cunik(cfg).start()

    @staticmethod
    def list():
        """
        Return all created cunik and its simple information.
        Usage:
            >>> cuniks = CunikApi.list()
            >>> for cunik in cuniks:
            >>>     print(cunik["id"])
            >>>     print(cunik["create_time"])
            >>>     print(cunik["name"])
        """
        return [cunik_registry.query(i) for i in cunik_registry.get_id_list()]

    @staticmethod
    def info(cid):
        """
        Return all informations about a cunik.
        Usage:
            >>> id = 'acb123'
            >>> cunik = CunikApi.info(id)
            >>> print(cunik["id"])
            >>> print(cunik["create_time"])
            >>> print(cunik["name"])
            >>> print(cunik["params"])
            >>> print(cunik["params"]["ipv4_addr"])
        """
        return cunik_registry.query(cid)

    @staticmethod
    def stop(cid):
        """
        Stop a running cunik.
        Usage:
            >>> id = 'acb123'
            >>> CunikApi.stop(id)
        """
        cunik_registry.query(cid).stop()

    @staticmethod
    def remove(cid):
        """
        (Stop and) Remove a created cunik.
        Usage:
            >>> id = 'acb123'
            >>> CunikApi.remove(id)
        """
        cunik_registry.remove(cunik_registry.query(cid))
