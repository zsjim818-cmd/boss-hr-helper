"""Replaceable browser adapter interface."""
from abc import ABC, abstractmethod

from browser.contracts import BrowserHealth, CandidateCard, CandidateDetail, GreetingOutcome


class BrowserAdapter(ABC):
    """A connected recruitment page source; implementations own no business DB."""

    @abstractmethod
    def connect(self) -> BrowserHealth:
        ...

    @abstractmethod
    def close(self) -> None:
        ...

    @abstractmethod
    def health(self) -> BrowserHealth:
        ...

    @abstractmethod
    def list_cards(self, page: int = 0, page_size: int = 20) -> list[CandidateCard]:
        ...

    @abstractmethod
    def open_detail(self, identity: str) -> CandidateDetail:
        ...

    @abstractmethod
    def send_greeting(self, identity: str, message: str, *, dry_run: bool = True) -> GreetingOutcome:
        ...
