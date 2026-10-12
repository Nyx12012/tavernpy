class Error(Exception):
    pass


class ConsoleTimeoutException(Error):
    pass


class ConsoleAlreadyCreatedException(Error):
    pass


class ConsoleCreateFailedException(Error):
    pass


class ConsolePermissionsDenied(Error):
    pass


class CommandTimeoutException(Error):
    pass


class ConsoleNotConnectedException(Error):
    pass


class ConsoleClosedException(Error):
    pass


class CommandFailedException(Error):
    def __init__(self, message, reply=None):
        super().__init__(message)
        self.reply = reply


class BadPlayerNameException(Error, ValueError):
    pass
