from abc import ABC, abstractmethod

class BaseProvider(ABC):
    """
    Every model provider must inherit this class and implement
    all abstract methods. This ensures all providers are
    interchangeable across the pipeline.
    """

    @abstractmethod
    def complete(self, system_prompt: str, user_message: str) -> str:
        """
        Send a prompt to the model and return the response as a string.
        """
        pass

    @abstractmethod
    def get_embedding(self, text: str, is_query: bool = False) -> list[float]:
        """
        Convert text into a vector embedding for semantic search.
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """
        Return the name of the provider.
        """
        pass