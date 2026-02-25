import tenno_reporter

if __name__ == "__main__":
    print("Cloud runner started. Running without GUI...")
    bot = tenno_reporter.HeadlessReporter()
    bot.loop_forever()