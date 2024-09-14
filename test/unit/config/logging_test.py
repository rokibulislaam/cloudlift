from cloudlift.config.logging import (log, log_bold, log_err, log_intent,
                                      log_intent_err, log_warning,
                                      log_with_color)


def test_log_with_color(capsys):
    log_with_color("Test message", "blue")
    captured = capsys.readouterr()
    assert "Test message" in captured.out


def test_log_err(capsys):
    log_err("Error message")
    captured = capsys.readouterr()
    # TODO: fix the original function to log to stderr
    assert "Error message" in captured.out


def test_log(capsys):
    log("Log message")
    captured = capsys.readouterr()
    assert "Log message" in captured.out


def test_log_warning(capsys):
    log_warning("Warning message")
    captured = capsys.readouterr()
    assert "Warning message" in captured.out


def test_log_bold(capsys):
    log_bold("Bold message")
    captured = capsys.readouterr()
    assert "Bold message" in captured.out


def test_log_intent(capsys):
    log_intent("Intent message")
    captured = capsys.readouterr()
    assert "Intent message" in captured.out


def test_log_intent_err(capsys):
    log_intent_err("Intent error message")
    captured = capsys.readouterr()
    # TODO: fix the original function to log to stderr
    assert "Intent error message" in captured.out
