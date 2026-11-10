"""Memory 领域错误。"""

from __future__ import annotations


class MemoryError(Exception):
    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


class MemoryPolicyDeniedError(MemoryError):
    def __init__(self, reason: str) -> None:
        super().__init__("memory.policy_denied", reason)


class MemoryTenantDeniedError(MemoryError):
    def __init__(self, reason: str) -> None:
        super().__init__("memory.tenant_denied", reason)


class MemoryScopeDeniedError(MemoryError):
    def __init__(self, reason: str) -> None:
        super().__init__("memory.scope_denied", reason)


class MemoryNotFoundError(MemoryError):
    def __init__(self, reason: str) -> None:
        super().__init__("memory.not_found", reason)
