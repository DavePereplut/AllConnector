from __future__ import annotations


class FrameworkError(Exception):
    """Base class for framework-specific errors."""


class ConnectionErrorBase(FrameworkError):
    """Base class for connection-related errors."""


class DeviceCreationError(FrameworkError):
    """Raised when a device instance cannot be created."""


class ConnectionOpenError(ConnectionErrorBase):
    """Raised when a connection cannot be established."""


class AuthenticationError(ConnectionOpenError):
    """Raised when authentication fails."""


class HostKeyError(ConnectionOpenError):
    """Raised when host key validation fails."""


class ConnectionClosedError(ConnectionErrorBase):
    """Raised when the connection is unexpectedly closed."""


class ReconnectFailedError(ConnectionErrorBase):
    """Raised when reconnect+retry fails."""


class PromptTimeoutError(ConnectionErrorBase):
    """Raised when an expected prompt is not seen in time."""


class PromptMismatchError(ConnectionErrorBase):
    """Raised when the prompt is not one of the expected prompts."""


class CommandExecutionError(FrameworkError):
    """Raised when a command fails to execute correctly."""


class CommandParseError(FrameworkError):
    """Raised when command output cannot be parsed."""


class EventWaitTimeoutError(FrameworkError):
    """Raised when event waiting times out."""


class InteractiveFlowError(FrameworkError):
    """Raised when an interactive dialogue fails."""


class FileTransferError(FrameworkError):
    """Raised when file transfer fails."""


class FileVerificationError(FileTransferError):
    """Raised when transferred file verification fails."""