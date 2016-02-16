# Author: Danilo Piparo, Enric Tejedor 2015
# Copyright CERN

"""CERN Specific Spawner class"""

import subprocess
import os
from dockerspawner import SystemUserSpawner
from tornado import gen
from traitlets import Unicode


class CERNSpawner(SystemUserSpawner):

    lcg_view_path = Unicode(
        default_value='/cvmfs/sft.cern.ch/lcg/views',
        config=True,
        help='Path where LCG views are stored in CVMFS.'
    )

    lcg_rel_field = Unicode(
        default_value='LCG-rel',
        help='LCG release field of the Spawner form.'
    )

    platform_field = Unicode(
        default_value='platform',
        help='Platform field of the Spawner form.'
    )


    def options_from_form(self, formdata):
        options = {}
        options[self.lcg_rel_field]   = formdata[self.lcg_rel_field][0]
        options[self.platform_field]  = formdata[self.platform_field][0]
        return options

    def _env_default(self):
        env = super(CERNSpawner, self)._env_default()

        env.update(dict(
            ROOT_LCG_VIEW_PATH     = self.lcg_view_path,
            ROOT_LCG_VIEW_NAME     = 'LCG_' + self.user_options[self.lcg_rel_field],
            ROOT_LCG_VIEW_PLATFORM = self.user_options[self.platform_field]
        ))

        return env

    @gen.coroutine
    def start(self, image=None):
        """Start the container and perform the operations necessary for mounting
        EOS.
        """
        username = self.user.name

        # Obtain credentials for the user
        subprocess.call([os.environ["AUTHSCRIPT"], username])
        self.log.debug("We are in CERNSpawner. Credentials for %s were requested.", username)

        # Create a temporary home for the user.
        home_dir = "/home/%s" %username
        subprocess.call(["mkdir","-p", home_dir])
        subprocess.call(["chown", username, home_dir])

        #tornadoFuture = super(CERNSpawner, self).start(
            #image=image
        #)

        #def get_and_bind_ticket(dummy):
            #resp = self.docker('inspect_container',self.container_id)
            #if not resp:
                #self.log.warn("Container not found")
            #container = resp.result()
            #container_pid = container['State']['Pid']


        #tornadoFuture.add_done_callback(get_and_bind_ticket)
        #yield tornadoFuture
