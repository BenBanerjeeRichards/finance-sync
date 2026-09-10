from abc import ABC, abstractmethod

from sqlalchemy.orm import Session


class BasePoster(ABC):

    @abstractmethod
    def run(self, session: Session) -> None:
        pass
