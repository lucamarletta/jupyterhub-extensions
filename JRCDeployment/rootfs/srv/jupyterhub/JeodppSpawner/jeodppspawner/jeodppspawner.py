# Author: Luca Marletta, Franck Eyraud -  August 2020

"""JEODPP Specific Spawner class"""

import contextlib
import json
import os
import pwd
import random
import re
import time
from socket import (
    socket,
    SO_REUSEADDR,
    SOL_SOCKET,
    gethostname,
)

import psutil
from JeoClasses.userform import Userform
from jinja2 import Environment, FileSystemLoader
from tornado import gen
from traitlets import (
    Unicode,
    Bool,
    Dict
)


# STAGE_DIR = os.environ['STAGE_DIR']
# print(" ".join(["STAGE_DIR: in jeodppspawner.py line 32", STAGE_DIR]))

def define_JeodppSpawner_from(base_class):
    """
        The Spawner need to inherit from a proper upstream Spawner (i.e Docker or Kube).
        But since our personalization, added on top of those, is exactly the same for all,
        by allowing a dynamic inheritance we can re-use the same code on all cases.
        This function returns our JeodppSpawner, inheriting from a class (upstream Spawner)
        given as parameter.
    """

    class JeodppSpawner(base_class):

        lcg_rel_field = Unicode(
            default_value='LCG-rel',
            help='LCG release field of the Spawner form.'
        )

        user_presetid = Unicode(
            default_value='presetid',
            help='Presetid selected for the Spawner form.'
        )

        # user_n_cores = Unicode(
        #     default_value='ncores',
        #     help='User number of cores field of the Spawner form.'
        # )
        #
        # user_memory = Unicode(
        #     default_value='memory',
        #     help='User available memory field of the Spawner form.'
        # )

        options_form_config = Unicode(
            config=True,
            help='Path to configuration file for options_form rendering.'
        )

        options_form_per_user = Unicode(
            config=True,
            help='Json object with the user form generated by UserForm class.'
        )

        options_form_template = Unicode(
            config=True,
            help='Path to template file for options_form rendering.'
        )

        init_k8s_user = Unicode(
            config=True,
            help='Script to authenticate with k8s clusters.'
        )

        # local_home = Bool(
        #     default_value=False,
        #     config=True,
        #     help="If True, a physical directory on the host will be the home and not eos."
        # )

        eos_path_prefix = Unicode(
            default_value='/eos/user',
            config=True,
            help='Path in eos preceeding the /t/theuser directory (e.g. /eos/user, /eos/scratch/user).'
        )

        k8s_config_script = Unicode(
            default_value='/cvmfs/sft.cern.ch/lcg/etc/hadoop-confext/k8s-setconf.sh',
            config=True,
            help='Path in CVMFS of the script to configure a K8s cluster.'
        )

        spark_cluster_field = Unicode(
            default_value='spark-cluster',
            help='Spark cluster name field of the Spawner form.'
        )

        # available_cores = List(
        #     default_value=['1'],
        #     config=True,
        #     help='List of cores options available to the user'
        # )
        #
        # available_memory = List(
        #     default_value=['8'],
        #     config=True,
        #     help='List of memory options available to the user'
        # )

        shared_volumes = Dict(
            config=True,
            help='Volumes to be mounted with a "shared" tag. This allows mount propagation.',
        )

        extra_env = Dict(
            config=True,
            help='Extra environment variables to pass to the container',
        )

        check_cvmfs_status = Bool(
            default_value=True,
            config=True,
            help="Check if CVMFS is accessible. It only works if CVMFS is mounted in the host (not the case in ScienceBox)."
        )

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

            self.node_selector = {}
            self.uc = ''
            self.modify_pod_class = ''
            self.image = ''
            self.presetid = ''
            self.pod_name = ''
            # self.cpu_limit = float()
            self.cpu_request = float(0.5)
            # self.mem_limit = ''
            self.mem_request = '5G'
            self.offload = False
            self.this_host = gethostname().split('.')[0]

            self.options_form = self._render_templated_options_form

        # def render_form(self, form_tmpl, my_image):
        #     form = form_tmpl  # here the form substitutions
        #     return form

        def options_from_form(self, formdata):
            options = {self.lcg_rel_field: formdata[self.lcg_rel_field][0],
                       self.user_presetid: formdata[self.user_presetid][0],
                       self.spark_cluster_field: 'none'}
            # options[self.user_n_cores] = int(formdata[self.user_n_cores][0]) \
            #     if formdata[self.user_n_cores][0] in self.available_cores else int(self.available_cores[0])
            # options[self.user_memory] = formdata[self.user_memory][0] + 'G' \
            #     if formdata[self.user_memory][0] in self.available_memory else self.available_memory[0] + 'G'

            return options

        def get_env(self):
            """
            Set base environmental variables
            """
            env = super().get_env()

            username = self.user.name
            userid = pwd.getpwnam(username).pw_uid

            # access_rights = 0o755
            homepath = "/home/%s" % (username)
            # os.makedirs(homepath, access_rights)

            env.update(dict(
                ROOT_LCG_VIEW_NAME=self.user_options[self.lcg_rel_field],
                USER=username,
                USER_ID=str(userid),
                HOME=homepath,
                SERVER_HOSTNAME=os.uname().nodename,

                JPY_USER=self.user.name,
                JPY_COOKIE_NAME=self.user.server.cookie_name,
                JPY_BASE_URL=self.user.base_url,
                JPY_HUB_PREFIX=self.hub.base_url,
                JPY_HUB_API_URL=self.hub.api_url
            ))

            if self.extra_env:
                env.update(self.extra_env)

            return env

        @gen.coroutine
        def stop(self, now=False):
            """ Overwrite default spawner to report stop of the container """

            if self._spawn_future and not self._spawn_future.done():
                # Return 124 (timeout) exit code as container got stopped by jupyterhub before successful spawn
                container_exit_code = "124"
            else:
                # Return 0 exit code as container got stopped after spawning correctly
                container_exit_code = "0"

            stop_result = yield super().stop(now)

            self._log_metric(
                self.user.name,
                self.this_host,
                ".".join(["exit_container", "exit_code"]),
                container_exit_code
            )

            return stop_result

        @gen.coroutine
        def poll(self):
            """ Overwrite default poll to get status of container """
            container_exit_code = yield super().poll()

            # None if single - user process is running.
            # Integer exit code status, if it is not running and not stopped by JupyterHub.
            if container_exit_code is not None:
                exit_return_code = str(container_exit_code)
                if exit_return_code.isdigit():
                    value_cleaned = exit_return_code
                else:
                    result = re.search('ExitCode=(\d+)', exit_return_code)
                    if not result:
                        raise Exception("unknown exit code format for this Spawner")
                    value_cleaned = result.group(1)

                self._log_metric(
                    self.user.name,
                    self.this_host,
                    ".".join(["exit_container", "exit_code"]),
                    value_cleaned
                )

            return container_exit_code

        @gen.coroutine
        def start(self):
            """Start the container and perform the operations necessary for mounting
            EOS, authenticating HDFS and authenticating K8S.
            """
            # from JeoClasses.userform import Userform
            global uc, cpu_limit, cpu_request, mem_limit, mem_request, modify_pod_class, node_selector, image
            user_form = Userform(self.user.name, self.options_form_per_user)
            options_form_config = user_form.user_form()
            self.log.info(" ".join(["options_form_config HERE", json.dumps(options_form_config)]))

            self.log.info(" ".join(["config_dir JeodppSpawner HERE", base_class.config_dir]))

            ## Here the form preset is selected
            username = self.user.name

            self.service_account = base_class.service_account            # self.presetid = self.user_options[self.image_field]
            self.presetid = self.user_options[self.user_presetid]
            self.log.info(" ".join(["Preset selected HERE", json.dumps(self.presetid)]))
            self.log.info(" ".join(["user_options HERE", json.dumps(self.user_options)]))

            stagedev = '-' + base_class.stage.lower() if base_class.stage.lower() == 'dev' else ''
            self.log.info(" ".join(["Stage HERE", base_class.stage.lower(), stagedev]))

            # Define the pod name directly, without using POD_NAME_TEMPLATE from Kubespawner
            # pod name must be lowercase with "-" in case needed
            self.log.info(" ".join(["base_class.stage", base_class.stage.lower()]))
            self.log.info(" ".join(["self.config_dir", self.config_dir]))
            podname_suffix = '-' + base_class.stage.lower() if base_class.stage.lower() == 'dev' else ''
            if self.presetid:
                self.pod_name = "jpy" + podname_suffix + "-" + username + "-" + self.presetid
            else:
                self.pod_name = "jpy" + podname_suffix + "-" + username

            for label, presetids in options_form_config.items():
                if self.presetid in presetids.keys():
                    preset_entry = presetids[self.presetid]['parameters']
                    image = preset_entry["imagename"]
                    self.log.debug(" ".join(["Imagename:", image]))
                    # uc = preset_entry["uc"] if 'uc' in preset_entry.keys() else username
                    if 'uc' in preset_entry.keys():
                        self.uc = preset_entry["uc"]
                        self.namespace = preset_entry["uc"] + stagedev
                        self.log.debug(" ".join(["HERE NAMESPACE:", self.namespace]))

                    if 'cpu_limit' in preset_entry.keys():
                        cpu_limit = float(preset_entry["cpu_limit"])
                        self.cpu_limit = cpu_limit
                    if 'cpu_request' in preset_entry.keys():
                        cpu_request = float(preset_entry["cpu_request"])
                        self.cpu_request = cpu_request
                    if 'mem_limit' in preset_entry.keys():
                        mem_limit = preset_entry["mem_limit"]
                        self.mem_limit = mem_limit
                    if 'mem_request' in preset_entry.keys():
                        mem_request = preset_entry["mem_request"]
                        self.mem_request = mem_request
                    if 'host_selector' in preset_entry.keys():
                        host_selector = preset_entry["host_selector"]
                        node_selector = {"kubernetes.io/hostname": host_selector}
                        self.node_selector = node_selector
                    elif '#node_selector' in preset_entry.keys():
                        node_selector = preset_entry["#node_selector"]
                        self.log.info(" ".join(["HERE #node_selector", json.dumps(node_selector)]))
                        self.node_selector = node_selector

                    if 'modify_pod_class' in preset_entry.keys():
                        modify_pod_class = preset_entry["modify_pod_class"]
                        self.modify_pod_class = modify_pod_class

                    self.log.info(" ".join(["HERE #node_selector", json.dumps(self.node_selector),
                                            "uc", self.uc,
                                            "cpu_limit", str(self.cpu_limit),
                                            "cpu_request", str(self.cpu_request),
                                            "mem_limit", str(self.mem_limit),
                                            "mem_request", str(self.mem_request),
                                            "modify_pod_class", self.modify_pod_class]))

            self.log.debug("%s", json.dumps(self.user_options), exc_info=True)
            ## The line below must be kept because this var is called from swanhub
            lcg_rel = self.user_options[self.lcg_rel_field]

            try:
                # self.uc = uc

                # self.cpu_limit = float(cpu_limit)
                #self.cpu_request = float(cpu_request)

                # self.mem_limit = mem_limit
                #self.mem_request = mem_request

                # if 'modify_pod_class' in preset_entry.keys():
                #     self.modify_pod_class = modify_pod_class

                self.image = image

                # Enabling GPU for cuda stacks
                # Options to export nvidia device can be found in https://github.com/NVIDIA/nvidia-container-runtime#nvidia_require_
                if "cu" in self.user_options[self.lcg_rel_field]:
                    self.env[
                        'NVIDIA_VISIBLE_DEVICES'] = 'all'  # We are making visible all the devices, if the host has more that one can be used.
                    self.env['NVIDIA_DRIVER_CAPABILITIES'] = 'compute,utility'
                    self.env['NVIDIA_REQUIRE_CUDA'] = 'cuda>=10.0 driver>=410'
                    if hasattr(self, 'extra_host_config'):  # for docker but not for kuberneters
                        self.extra_host_config.update({'runtime': 'nvidia'})
                    if hasattr(self, 'extra_resource_guarantees'):  # for kubernetes but not for docker
                        self.extra_resource_guarantees = {"nvidia.com/gpu": "1"}

                start_time_start_container = time.time()

                # start configured container
                startup = yield super().start()

                return startup
            except BaseException as e:
                self.log.error("Error while spawning the user container: %s", e, exc_info=True)
                raise e

        def _render_templated_options_form(self, spawner):
            """
            Render them form ad a template based on options_form_config json config file
            """
            templates_dir = os.path.dirname(self.options_form_template)
            env = Environment(loader=FileSystemLoader(templates_dir))
            template = env.get_template(os.path.basename(self.options_form_template))

            try:
                ## self.options_form_per_user is the dir with Config files
                self.log.error("initialize form: %s, %s", self.user.name, self.options_form_per_user, exc_info=True)
                #                with open(self.options_form_config) as json_file:
                #                    options_form_config = json.load(json_file)
                # from JeoClasses.userform import Userform
                user_form = Userform(self.user.name, self.options_form_per_user)
                options_form_config = user_form.user_form()
                # self.log.info(" ".join(["options_form_config HERE", json.dumps(options_form_config)]))

                # return template.render(options_form_config=self.options_form_config)
                return template.render(options_form_config=options_form_config)
            except Exception as ex:
                self.log.error("Could not initialize form: %s", ex, exc_info=True)
                raise RuntimeError(
                    """
                    Could not initialize form, invalid format
                    """)

        def _render_per_user_form(self):
            """
            Render them form base from the obj per user generated by the UserForm class.
            """
            try:
                return template.render(self.options_form_per_user)
            except Exception as ex:
                self.log.error("Could not initialize form: %s", ex, exc_info=True)
                raise RuntimeError(
                    """
                    Could not initialize form, invalid format
                    """)

        @property
        def volume_mount_points(self):
            """
            Override this method to take into account the "shared" volumes.
            """
            return self.get_volumes(only_mount=True)

        @property
        def volume_binds(self):
            """
            Since EOS now uses autofs (mounts and unmounts mount points automatically), we have to mount
            eos in the container with the propagation option set to "shared".
            This means that: when users try to access /eos/projects, the endpoint will be mounted automatically,
            and made available in the container without the need to restart the session (it gets propagated).
            This also means that, if the user endpoint fails, when it gets back up it will be made available in
            the container without the need to restart the session.
            The Spawnwer/dockerpy do not support this option. But, if volume_bins return a list of string, they will
            pass the list forward until the container construction, without checking or trying to manipulate the list.
            """
            return self.get_volumes()

        def get_volumes(self, only_mount=False):

            def _fmt(v):
                return self.format_volume_name(v, self)

            def _convert_list(volumes, binds, mode="rw"):
                for k, v in volumes.items():
                    m = mode
                    if isinstance(v, dict):
                        if "mode" in v:
                            m = v["mode"]
                        v = v["bind"]

                    if only_mount:
                        binds.append(_fmt(v))
                    else:
                        binds.append("%s:%s:%s" % (_fmt(k), _fmt(v), m))
                return binds

            binds = _convert_list(self.volumes, [])
            binds = _convert_list(self.read_only_volumes, binds, mode="ro")
            return _convert_list(self.shared_volumes, binds, mode="shared")

        @staticmethod
        def get_reserved_port(start, end, n_tries=10):
            """
                Reserve a random available port.
                It puts the door in TIME_WAIT state so that no other process gets it when asking for a random port,
                but allows processes to bind to it, due to the SO_REUSEADDR flag.
                From https://github.com/Yelp/ephemeral-port-reserve
            """
            for i in range(n_tries):
                try:
                    with contextlib.closing(socket()) as s:
                        port = random.randint(start, end)
                        net_connections = psutil.net_connections()
                        # look through the list of active connections to check if the port is being used or not and return FREE if port is unused
                        if next((conn.laddr[1] for conn in net_connections if conn.laddr[1] == port), 'FREE') != 'FREE':
                            raise Exception('Port {} is in use'.format(port))
                        s.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
                        s.bind(('127.0.0.1', port))

                        # the connect below deadlocks on kernel >= 4.4.0 unless this arg is greater than zero
                        s.listen(1)

                        sockname = s.getsockname()

                        # these three are necessary just to get the port into a TIME_WAIT state
                        with contextlib.closing(socket()) as s2:
                            s2.connect(sockname)
                            s.accept()
                            return sockname[1]
                except:
                    if i == n_tries - 1:
                        raise

        def _log_metric(self, user, host, metric, value):
            self.log.info("user: %s, host: %s, metric: %s, value: %s" % (user, host, metric, value))

    return JeodppSpawner
