#!/usr/bin/python
# -*- coding: utf-8 -*-
'''
    PyUnicoreManager (It adapts PyUnicore library and uses it from different frameworks)
    Author: Aarón Pérez Martín
    Contact:a.perez.martin@fz-juelich.de
    Organization: Forschungszentrum Jülich

    PyUnicore library (Client for UNICORE using the REST API)
    For full info of the REST API, see https://sourceforge.net/p/unicore/wiki/REST_API/
'''
#

import pyunicore.client as unicore_client
from base64 import b64encode
import os, time, sys, logging, datetime
from enum import IntEnum
import ipywidgets, time


def set_logger(name, level='INFO'):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    # Configure stream handler for the cells
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s - %(message)s', '%H:%M:%S'))
    handler.setLevel(level)

    logger.addHandler(handler)
    return logging.root.manager.loggerDict[name]


logss = set_logger("ndp.pyunim")


UNICORE_projects_errors = {"cslns":"slns"}

class Method(IntEnum):
    # Different access require a different token and the way to call a job
    ACCESS_LDAP = 0
    ACCESS_COLLAB = 1


class Authentication():
    def __init__(self, token: str, access: Method, server: str, **args):
        self.token = token
        self.access = access
        self.server = server


class Utils():
    @staticmethod
    def execution_time(start, end, num_dec):
        total_time = end - start
        if total_time < 60:
            msg = "Flow time --- " + str(round(total_time, num_dec)) + " seconds ---"
        elif 60 <= total_time < 3600:
            msg = "Flow time --- " + str(round(total_time / 60, num_dec)) + " minutes ---"
        elif 3600 <= total_time < 86400:
            msg = "Flow time --- " + str(round(total_time / 3600, num_dec)) + " hours ---"
        else:
            msg = "Flow time --- " + str(round(total_time / 86400, num_dec)) + " days ---"
        return msg

    @staticmethod
    def generateToken(ldapaccount, ldappassword):
        return b64encode(str.encode(ldapaccount + ":" + ldappassword)).decode("ascii")

    @staticmethod
    def arrayToString(array):
        return ' '.join(map(str, array))

    @staticmethod
    def generate_PythonCompiler(variables):
        return [" cd " + variables["destination_project_path"],
                "date >> text; echo done!"]

    @staticmethod
    def generate_steps_bashscript(variables, filename):
        return ["cd " + variables["destination_project_path"],
                'log=' + variables["destination_log_path"] + '/log$(date "+%Y%m%d")',
                "chmod 764 " + filename + " >>$log ",
                "./ " + filename + ' >>$log']


class Environment_UNICORE():

    def __init__(self, auth: Authentication, **args):
        self.urls, self.conn_info, self.job_info, self.script_info = {}, {}, {}, {}

        # Required for a sever connection
        self.conn_info["token"] = auth.token
        self.conn_info["methodToAccess"] = auth.access
        self.conn_info["serverToConnect"] = auth.server
        self.conn_info["serverToRegister"] = args.get('serverToRegister', "https://zam2125.zam.kfa-juelich.de:9112/HBP/rest/registries/default_registry")
        #unicore_client._HBP_REGISTRY_URL)

        # NOTE server_endpoint -> #Storage Endpoint: "PROJECT", "SCRATCH", "HOME",...

        env_dict = args.get('env_from_user', None)
        if env_dict and "JobArguments" in env_dict.keys():
            self.job_info.update(env_dict)  # it must contain "JobArguments"


class PyUnicoreManager(object):
    def __init__(self, environment, **args):

        self.storage = None
        self.env = environment

        self.verbose = args.get('verbose', False)
        self.check_environment = args.get('check_environment', False)
        self.clean_job_storages = args.get('clean_job_storages', False)
        has_errors = False

        if self.env.conn_info["token"] is None:
            logss.error("Token is required!")
            has_errors = True
        try:
            # Accesing with LDAP or COLLAB token have have different parameters in the PyUnicore.Transport
            # oidc=False doesnt work with collab token
            self.transport = None
            if Method.ACCESS_LDAP == self.env.conn_info["methodToAccess"]:
                self.transport = unicore_client.Transport(self.env.conn_info["token"], oidc=False)
            elif Method.ACCESS_COLLAB == self.env.conn_info["methodToAccess"]:
                self.transport = unicore_client.Transport(self.env.conn_info["token"])
            else:
                 logss.error("No access method selected")
            try:
                # Important: To be sure of selecting the proper internal mapping of UNICORE links to a specific project
                if "server_project" in self.env.job_info.keys():
                    self.transport.preferences = "group:" + str(self.env.job_info["server_project"])  # used to Upload/download files

                self.registry = unicore_client.Registry(self.transport, self.env.conn_info["serverToRegister"])
                self.site = self.registry.site(self.env.conn_info["serverToConnect"])

                self.client = unicore_client.Client(self.transport, self.site.resource_url)
            except Exception as e:
                logss.error("Server: " + str(self.env.job_info["server"]) + " is not responding. " + str(e))

            try:
                if "server_endpoint" in self.env.job_info.keys():
                    for storage in self.site.get_storages():
                        if storage.resource_url.endswith(self.env.job_info["server_endpoint"]):
                            self.storage = storage
                            break
            except Exception as e:
                logss.error("Source not available for " + self.env.job_info["JobArguments"]["Project"])
                return None

            #if not self.storage and ("server_endpoint" not in self.env.job_info.keys()):
            #    logss.error("Source not available " + self.env.job_info["server_endpoint"])
            #    has_errors = True
            #    has_errors = True

            if self.clean_job_storages:
                self.clean_storages(endswith="-uspace")

            # Get the object Storage
            # Endpoint of Storage is mapped from env variables of your account into the UNICORE

            # we need to access to the right project folder into the HPC system
            # First, setting an environment variable by a job
            if self.check_environment:
                logss.info("Checking current $PROJECT into UNICORE system")
                job_cmd, result = self.one_run(
                    steps=["jutil env activate -p " + str(self.env.job_info["JobArguments"]["Project"]),
                           "echo $PROJECT"],
                    parameters=self.env.job_info["JobArguments"])

                if len(result["stderr"]) > 0:
                    logss.error("Error " + result["stderr"])
                    # has_errors = True

                logss.info("Variable $PROJECT on HPC is " + result["stdout"])

            if has_errors:
                logss.error("\nPlease provide the right parameters.")

        except Exception as e:
            logss.error("Error creating workflow: " + str(e))
            return None

    def check_env_vars_HPC(self):

        logss.info("Getting Environment variables on", self.env.job_info["server"])
        return self.one_run(steps=["printenv"], parameters=self.env.job_info["JobArguments"])

    def getStorage(self):
        if (self.verbose):
            for storage in self.client.get_storages():
                logss.info(storage.resource_url)

        return self.client.get_storages()

    def getJobs(self, status=None):
        result = []
        list = self.client.get_jobs()
        if status:
            for j in list:
                if j.properties['status'].lower() == status.lower():
                    result.append(j)
            return result
        else:
            return list

    def getSites(self):
        #return unicore_client.get_sites(self.transport)
        return self.registry.site_urls

    def clean_storages(self, endswith):
        count = 0
        if len(self.client.get_storages()) >= 200 and len(endswith) > 0:
            logss.info("Cleaning storage endpoints. Working on it...")
            for storage in self.client.get_storages():
                msg = ""
                if storage.resource_url.endswith(str(endswith)):
                    self.transport.delete(url=storage.resource_url)
                    count += 1
                    msg = str(storage.resource_url).split("/")[-1] + " has been removed"
                else:
                    msg = "Keep: " + str(storage.resource_url)

                if self.verbose:
                    logss.info(msg)
            logss.info("Storage endpoints removed: " + str(count))
        else:
            logss.info("Storage endpoints are still under limit: " + str(len(self.client.get_storages())))

    def createJob(self, list_of_steps, job_args=None):

        executable = ""
        for item in list_of_steps[:-1]:
            executable += item + " \n "
        executable += list_of_steps[-1]  # Last element can not contain the new line symbol

        if (self.verbose):
            logss.info("Executing commands...")
            for item in list_of_steps:
                logss.info(">" + str(item))

        job = dict(job_args) if job_args else dict()
        job['Executable'] = executable

        return job

    def __run_job(self, job, wait_process=True):
        result_job = {}
        try:
            cmd_job = self.client.new_job(job_description=job)

            logss.info("Job status... " + str(cmd_job.properties['status']))

            # Wait until the job finishes
            while (cmd_job.properties['status'] == "QUEUED"):
                time.sleep(0.25)

            logss.info(cmd_job.properties['status'])
            if wait_process:
                cmd_job.poll()

                wd = cmd_job.working_dir
                result_job["stderr"] = ''.join([x.decode('utf8') for x in wd.stat("/stderr").raw().readlines()])
                result_job["stdout"] = ''.join([x.decode('utf8') for x in wd.stat("/stdout").raw().readlines()])
                if self.verbose:
                    logss.info(result_job.items())
                if (cmd_job.properties['status'] == "FAILED"):
                    raise Exception(str(result_job["stderr"]))
                else:
                    logss.info('Job finished!')
            else:
                logss.info('Job is running!. You could stop it!')
        except Exception as e:
            logss.error("Job error:" + str(e))
            return None, None
        return cmd_job, result_job

    def uploadFiles(self, filesToUpload):
        try:
            if len(filesToUpload) == 0:
                logss.info("Nothing to upload")
                return

            # Uploading files
            logss.info("Uploading to " + str(self.storage.resource_url))
            list_files = list()

            for file_info in filesToUpload:
                filename = str(file_info[0]).split('/')[-1]

                # it works like this server_endpoint(PROJECT,SCRATCH,...)/folder/file
                self.storage.upload(str(file_info[0]), destination=os.path.join(file_info[1]))
                if (self.verbose):
                    logss.info(" - Uploaded file " + filename)  # getting the last element
                list_files.append(filename)
            logss.info("Uploaded all files: " + str(list_files))
            return True
        except Exception as e:
            logss.error("Uploading error", e)
            return False

    def downloadFiles(self, filesToDownload):
        try:
            if len(filesToDownload) == 0:
                logss.info("Nothing to download")
                return

            logss.info("Downloading from " + self.storage.resource_url)
            list_files = list()
            for file_info in filesToDownload:
                filename = str(file_info[1]).split('/')[-1]  # getting the last element
                remote = self.storage.stat(file_info[1])  # internal links works like this ../PROJECT/ "collab/filename"
                remote.download(os.path.join(file_info[0]))
                if (self.verbose):
                    logss.info(" - Downloaded file" + filename)
                list_files.append(filename)
            logss.info("Downloaded all files: " + str(list_files))
            return True
        except Exception as e:
            logss.error("Downloading error", str(e))
            return False

    def one_run(self, steps, parameters, wait_process=True):
        if len(steps) == 0:
            logss.info("No instructions to execute")
            return
        # Executing a job
        if self.verbose:
            logss.info("STEPS: " + str(steps))
            logss.info("ARGUMENTS: " + str(parameters))

        job = self.createJob(list_of_steps=steps, job_args=parameters)
        cmd_job, result_job = self.__run_job(job, wait_process)
        return cmd_job, result_job

    import ipywidgets, time

class GUI():
    
    #token=clb_oauth.get_token(), access=Method.ACCESS_COLLAB
    def __init__(self, token, **args):
        self.dashboard = None
        self.output = ipywidgets.Output()
        self.token = token
        self.verbose = args.get('verbose', False)
        self.server = args.get('server', "JUSUF")   
        self.server_endpoint = args.get('server_endpoint', "SCRATCH")     
        self.access = args.get('access', Method.ACCESS_COLLAB)
        self.cmd_separator = args.get('cmd_separator', ',')
        
        self.job_finish = False
        self.job_log = ipywidgets.Textarea(value="",placeholder='result of the job',description='Job log:',rows=1, layout=ipywidgets.Layout(width="100%", height="auto"))
        self.__user_data()
        self.tabs = {}
        
        self.create_tab_1()
        
    def extract_job_details(self):
        a=1
        #setup["server"]= self.server
        #setup["server_endpoint"] = self.server_endpoint 
        # setup["server_project"] = "cslns"
        # setup["JobArguments"]=""#"Project": "slns", 'Resources': {"Queue": "develgpus", "Nodes" : "1","Runtime" : "10m"}}
        
    def __user_data(self):
        
        #Minimal data struture
        setup={"server":self.server, "server_endpoint":self.server_endpoint }                
        self.authentication = Authentication(token=self.token, access=self.access, server=self.server)
        self.pym = PyUnicoreManager(environment=Environment_UNICORE(auth=self.authentication, env_from_user=setup), clean_job_storages=True, verbose=self.verbose)
        
        self.servers = self.pym.getSites().keys()
        self.endpoints =  [k.resource_url.split('/')[-1] for k in self.pym.getStorage() if not k.resource_url.endswith("-uspace")] #["HOME", "PROJECT",  "SCRATCH"]
        self.access_list = [e.name for e in Method]
        #methods = [e.name for e in Method]
        
        
        #self.servers   = ["JUWELS","JUDAC","JUSUF","JURECA"] # "DAINT-CSCS", "CINECA-M100", "CINECA-MARCONI", "CINECA-G100'"
        #self.endpoints = ["HOME", "PROJECT",  "SCRATCH"]        
        #self.access_list = ['ACCESS_LDAP', 'ACCESS_COLLAB']
        #self.user_info = ""#pym.client.access_info()
        queues = list(self.pym.client.get_compute()[0].get_queues().keys())
        
        self.user_info = {"username":self.pym.client.access_info()['xlogin']['UID'],
                         "email":self.pym.client.access_info()['dn'].split('=')[-1],
                         "projects":self.pym.client.access_info()['xlogin']['availableGroups'],
                         "def_project":self.pym.client.access_info()['xlogin']['group'],
                         "queues": queues,
                         "def_queue":self.pym.client.access_info()['queues']['selected']}
        #self.user = 'perezmartin1' #self.user_info['xlogin']['UID']
        #self.email= "a.perez.martin@fz-juelich.de" #self.user_info['dn'].split('=')[-1]
        #self.projects = ['cslns'] #self.user_info['xlogin']['availableGroups']
        #self.def_project='cslns'# self.user_info['group']
        #self.queues = ['gpus', 'batch', 'develgpus'] #self.user_info['queues']['availableQueues']
        #self.def_queue = 'gpus'#self.user_info['queues']['selected']
        
    def change_children(self,obj):

        if not self.job_finish:
            if self.dashboard.children[0].selected_index == 0:
                self.create_tab_2()
            elif self.dashboard.children[0].selected_index == 1:
                self.create_tab_3()
            elif self.dashboard.children[0].selected_index == 2:            
                self.launch_job()
                self.create_tab_4()
                #with self.output:
            #    print("Errors, check tabs")
        #with self.output:
        #    print("Object",obj.value)
        display(self.output)
        
######### TAB 1 ######### 
    def create_tab_1(self):
        self.output.clear_output()
        visual_obj_1 = ipywidgets.Text(value=self.user_info["username"],placeholder='',description='Username:',disabled=True)
        visual_obj_2 = ipywidgets.Text(value=self.user_info["email"],placeholder='',description='Email:',disabled=True)
        
        visual_obj_3 = ipywidgets.Dropdown(options=self.servers,value=self.server,description='Server:', disabled=False,)
        visual_obj_4 = ipywidgets.Dropdown(options=self.endpoints,value=self.endpoints[0],description='Site:',disabled=False,)
        btn = ipywidgets.Button(description='Select server',button_style='Primary',icon = 'cog')
        btn.on_click(self.change_children)
        
        self.tabs[0] = [visual_obj_1,visual_obj_2,visual_obj_3, visual_obj_4]
        vb = ipywidgets.VBox(children=[ipywidgets.HBox(children=[visual_obj_1,visual_obj_2]),
                                      ipywidgets.HBox(children=[visual_obj_3,visual_obj_4])])
        tabs =  ipywidgets.Tab(children=[vb])
        tabs.set_title(0,"HPC Server")

        self.dashboard = ipywidgets.VBox([tabs,btn]) #,btn
        with self.output:
            display(self.dashboard)
        display(self.output)


######### TAB 2 ######### 
    def create_tab_2(self):
        self.output.clear_output()
        
        visual_obj_1 = ipywidgets.Dropdown(options=self.user_info["projects"],value=self.user_info["def_project"],description='Project:', disabled=False,)
        visual_obj_2 = ipywidgets.Dropdown(options=self.user_info["queues"],value=self.user_info["def_queue"],description='Queue:',disabled=False,)
        
        visual_obj_3 = ipywidgets.widgets.IntText(value=1,description='Nodes:',disabled=True, layout=ipywidgets.Layout(width='10%'))
        visual_obj_4 = ipywidgets.FloatSlider( value=.10,min=0.10,max=23.30,step=0.10,
                                          description='Time( h:m):',disabled=False,continuous_update=False,
                                          orientation='horizontal',readout=True,readout_format='.2f',)
        
        btn = ipywidgets.Button(description='Select storage',button_style='Primary',icon = 'cogs')
        btn.on_click(self.change_children)

        self.tabs[1] = [visual_obj_1,visual_obj_2,visual_obj_3,visual_obj_4]
        tabs = self.dashboard.children[0]
        tabs.children = tuple(list(tabs.children)+ [ipywidgets.HBox(children=[visual_obj_1,visual_obj_2,visual_obj_3,visual_obj_4])])
        tabs.set_title(0,"HPC Server")
        tabs.set_title(1,"HPC Storage"),

        tabs.selected_index=1
        self.dashboard = ipywidgets.VBox([tabs,btn])
        with self.output:
            display(self.dashboard)
        display(self.output)
        
######### TAB 3 ######### 
    
    def launch_job(self):
        """
        count=1
        while count<=10:
            time.sleep(1)
            self.progress_bar.value=count            
            self.job_log.value += self.job_log.value + "\n" + str(count)
            count+=1
        """
        #setup["server"]= self.server
        #setup["server_endpoint"] = self.server_endpoint 
        # setup["server_project"] = "cslns"
        # setup["JobArguments"]=""#"Project": "slns", 'Resources': {"Queue": "develgpus", "Nodes" : "1","Runtime" : "10m"}}
        self.progress_bar.value = 1
        self.setup={}
        self.setup["server"]= self.tabs[0][2].value
        self.setup["server_endpoint"] = self.tabs[0][3].value
        self.setup["server_project"] = self.tabs[1][0].value
        
        # to fix the UNICORE naming  project_name != project_folder
        project_correction =""
        if self.setup["server_project"] in UNICORE_projects_errors.keys():
            project_correction = UNICORE_projects_errors[self.setup["server_project"]]
        else:
            project_correction = self.setup["server_project"]
        
        self.setup["JobArguments"] = {"Project": project_correction, 'Resources': {"Queue": self.tabs[1][1].value, "Nodes" : str(self.tabs[1][2].value),"Runtime" : str(int(self.tabs[1][3].value*100))+"m"}}

        self.progress_bar.value = 2   
        self.authentication = Authentication(token=self.token, access=self.access, server=self.setup['server'])
        self.pym = PyUnicoreManager(environment=Environment_UNICORE(auth=self.authentication, env_from_user= self.setup), clean_job_storages=False, verbose=self.verbose)
        self.progress_bar.value = 3
        self.job_steps_str = str(self.tabs[2][0].value) #[1:-1]

        #self.job_steps_str = str(self.job_steps_str[1:-1])# removing simple quote from the widget.
        
        self.job_steps_list = []
        if len(self.job_steps_str) > 0:
            for command in self.job_steps_str.split(self.cmd_separator):
                self.job_steps_list.append(command.strip())
        
        job, result= self.pym.one_run(self.job_steps_list, self.setup["JobArguments"]) # ["pwd", "date"]
        """
        while count<=10 and result!=None:
            time.sleep(1)
            count+=1
        """
        if result == None:
            self.tabs[2][1].bar_style = 'danger'
        else:
            self.progress_bar.value = 4

            result_string = result['stdout'].split("\n")
            for x in result_string:
                if self.job_log.value == "":
                    self.job_log.value = x + "\n"
                else:
                    self.job_log.value += x + "\n"
                self.job_log.rows+=1

        with self.output:
            print(self.setup)
        display(self.output)
        
        
            
    def create_tab_3(self):
        self.output.clear_output()
        
        visual_obj_1 = ipywidgets.Text(value='pwd, date',placeholder='Type something',description='Commands:',disabled=False, layout=ipywidgets.Layout(width='50%'))
        self.progress_bar = ipywidgets.IntProgress(value=0,min=0,max=4,step=2,description='Executing:',bar_style='success',) # 'success', 'info', 'warning', 'danger' or ''orientation='horizontal') 
        btn = ipywidgets.Button(description='Execute job',button_style='Primary',icon = 'cloud-upload')
        btn.on_click(self.change_children)
        
        self.tabs[2] = [visual_obj_1, self.progress_bar]        
        tabs = self.dashboard.children[0]
        tabs.children = tuple(list(tabs.children)+ [ipywidgets.HBox(children=[visual_obj_1,self.progress_bar])])
        tabs.set_title(0,"HPC Server")
        tabs.set_title(1,"HPC Storage"),
        tabs.set_title(2,"HPC execution"),
        tabs.selected_index=2
        self.dashboard = ipywidgets.VBox([tabs,btn])
        with self.output:
            display(self.dashboard)
        display(self.output)
        
######### TAB 4 ######### 
#    def update_texarea(self):
#        self.job_log.rows = self.job_log.value.count('\n') + 1

    def create_tab_4(self):
        self.output.clear_output()

        btn = ipywidgets.Button(description='Check log',button_style='Primary',icon = 'code')
        btn.on_click(self.change_children)
        
        self.tabs[3] = [self.job_log]
        tabs = self.dashboard.children[0]
        tabs.children = tuple(list(tabs.children)+ [ipywidgets.HBox(children=[self.job_log])])
        tabs.set_title(0,"HPC Server")
        tabs.set_title(1,"HPC Storage"),
        tabs.set_title(2,"HPC execution"),
        tabs.set_title(3,"HPC logs"),
        tabs.selected_index=3
        self.dashboard = ipywidgets.VBox([tabs,btn])
        with self.output:
            display(self.dashboard)
        display(self.output)
        
        self.job_finish=True

#################

#gui = GUI()
