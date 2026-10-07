import json,sys,time
from pathlib import Path
from datetime import datetime,timezone
PROJECT=Path.home()/"Desktop"/"IMC_TRADING_MAIN"/"CORE"
sys.path.insert(0,str(PROJECT))
from mykonos_meme_scout.store import Store
DB=PROJECT/"data"/"imc-main.db"
STATUS=Path.home()/"Desktop"/"IMC_TRADING_MAIN"/"RUNTIME"/"learning_settler_v460_status.json"
def now():return datetime.now(timezone.utc).isoformat()
def write(obj):STATUS.write_text(json.dumps(obj,indent=2),encoding="utf-8")
def tick():
 s=Store(DB,1000)
 t=time.perf_counter()
 try:
  mature=s.label_mature_outcomes(batch_limit=500)
  predictions=s.label_and_reward_learning_predictions(batch_limit=500)
 finally:s.close()
 return mature,predictions,time.perf_counter()-t
def main():
 while True:
  try:
   a,b,sec=tick()
   write({"version":"4.6.0-learning-settler","state":"RUNNING","updated":now(),"authority":"0%","live_execution":False,
          "mature_outcomes_added":a,"prediction_outcomes_added":b,"last_tick_seconds":round(sec,3),"last_error":None})
   time.sleep(.5 if b>=500 else 5.0)
  except Exception as e:
   write({"version":"4.6.0-learning-settler","state":"ERROR","updated":now(),"authority":"0%","live_execution":False,
          "last_error":f"{type(e).__name__}: {e}"})
   time.sleep(5)
if __name__=="__main__":
 main()
