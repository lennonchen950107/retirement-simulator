"""一次跑完全部測試。執行:python tests/run_all.py(在 retirement_simulator/ 目錄)"""
import os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
bad = [t for t in ["test_engine", "test_llm_json", "test_followup", "test_followup_suite", "test_parse_suite", "test_llm_client", "test_hooks", "test_app"]
       if subprocess.run([sys.executable, os.path.join(here, t + ".py")], capture_output=True).returncode]
print("全部通過" if not bad else f"失敗:{bad}(單獨執行該檔案看詳細訊息)")
sys.exit(1 if bad else 0)
