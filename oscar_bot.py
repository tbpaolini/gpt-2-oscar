from src.interactive_conditional_samples import interact_model, STOP
from bot.text_process import post_process
import multiprocessing as mp
import threading as td
from time import sleep

SHUTDOWN = ("stop", "quit", "exit")

class OscarBot():
    
    def __init__(self) -> None:
        print("Starting OScar bot...")
        self.running = True
        
        self.input_queue = mp.Queue()
        self.output_queue = mp.Queue()
        self.question = ""
        self.response = ""
        model_process = mp.Process(
            target=interact_model,
            kwargs= {"input_queue": self.input_queue, "output_queue": self.output_queue},
        )
        model_process.start()

        input_thread = td.Thread(target=self.send_question)
        input_thread.start()
        output_thread = td.Thread(target=self.receive_response)
        output_thread.start()

        self.workers = (model_process, input_thread, output_thread)
        exit_listener  = td.Thread(target=self.clean_exit)
        exit_listener.start()

    def send_question(self):
        while self.running:
            self.question = input()
            if self.question in SHUTDOWN: break
            self.input_queue.put_nowait((self.question, 0))
    
    def receive_response(self):
        while True:
            self.response = self.output_queue.get()
            if self.response == STOP: break
            print(post_process(self.response[0]))
    
    def clean_exit(self, *args):
        while True:
            if self.question.lower() in SHUTDOWN: break
            sleep(1)
        
        print("Closing bot...")
        self.input_queue.put_nowait(STOP)
        self.running = False
        for worker in self.workers:
            worker.join()

if __name__ == "__main__":
    OscarBot()