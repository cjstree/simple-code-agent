from typing import Any

# provide background task management for agent bash tool.
# use start to register command. use collect to get the result of command.
# TODO: use asyncio to control the process.
class BackgroundManager :
    
    ready : dict[str,Any] # process ready to run, {id,process}
    running : dict          
    max_process : int
    time_out : int  # time limit for single process. 

    # generate an id for the cmd. Store the task into ready dict
    # spawn a asyncio process if len(running) < max_process
    def start(self,cmd : str) -> str:
        pass


    # collect the result of running process.
    # return a list of [id,result]
    # check the status of running process. If fail or TimeOut, Kill the process and store the info into result.
    # once the process terminate, remove the process from dict.
    # call self.run to run new task process.
    def collect(self) -> list[str,str]:
        pass

    # pick a ready process to run, until the len(ready) == 0 or len(running) == max_process
    def run(self):
        pass

