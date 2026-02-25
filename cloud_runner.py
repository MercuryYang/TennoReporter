# cloud_runner.py
import time
import tenno_reporter

if __name__ == "__main__":
    app = tenno_reporter.TennoReporter()
    app.running = True  # 强制开启监控模式

    print("Cloud runner started. Running TennoReporter without GUI...")

    while True:
        try:
            app._fetch_and_update()
        except Exception as e:
            print("Error during update:", e)
        time.sleep(10)  # 等价于 CHECK_EVERY