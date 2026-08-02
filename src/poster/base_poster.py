from abc import ABC, abstractmethod


class BasePoster(ABC):

    @abstractmethod
    def run(self) -> None:
        pass
