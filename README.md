# PyUnicoreManager Library

It is a Python wrapper of PyUnicore library (linked to UNICORE) that tries to minimize the lines of code and makes it flexible enough to be a interconnection piece of workflows. It is designed to be used from Notebooks of EBRAINS or a personal laptop with a LDAP authentication. In both cases, the user can launch jobs on the HPC systems, upload and download files and make complex workflow.

Development of this library was funded in part by the Human Brain Project

For more information about the Human Brain Project, see https://www.humanbrainproject.eu/

See LICENSE file for licensing information


# Important configuration for the framework
For this example we use a certain Server with a particular partition and project, allocating 1 node for 10 minutes for a single job.

    setup={}
    setup["server"]= myServer
    setup["server_endpoint"] = myPartition
    setup["JobArguments"]={"Project": myProject, 'Resources': {"Queue": myQueue, "Nodes" : "1","Runtime" : "10m"}}

# Authentication from EBRAINS notebook on the HPC system

    authentication = Authentication(token=clb_oauth.get_token(), access=Method.ACCESS_COLLAB, server=setup['server'])

# Authentication from personal PC on the HPC system

    myToken = b64encode(b"myUser:myPassword").decode("ascii")
    authentication = Authentication(token=myToken, access=Method.ACCESS_LDAP, server=setup['server'])

# Environemnt for the framework

    env = Environment_UNICORE(auth=authentication, env_from_user= setup)
   
# Instanciate the framework

It would check if the jobs storage list is full, in that case would clean it up.

    pym = PyUnicoreManager(environment=env, clean_job_storages=True, verbose=True)

# Launching a simple job

All the job steps would be a list of command lines. The "job" object contains important information and the "result" is a dictionary with the keys "stderr" and "stdout".

    job_steps = ["ls -la","pwd"]
    job, result= pym.one_run(job_steps, setup["JobArguments"])
    