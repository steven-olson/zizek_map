from src.deps.epub_utils import EpubUtils
from src.models.text_components import BookStructuredComponents


class BookComponentBreakdownService:

    @classmethod
    def get_book_components(cls, book_epub_path: str) -> BookStructuredComponents:
        """
        Given a filepath to a book, break down that book into it's constituent components, and
        return a BookStructuredComponents.

        This is done by passing the book,
        :param book_epub_path:
        :return:
        """
        # 1. Get the actual epub
        epub = cls._get_epub(book_epub_path)

        # 2. Get our corresponding prompt
        prompt = cls._get_prompt()

        # 3. Make the actual trip to the llm
        book_components = cls._make_llm_call(epub, prompt)

        return book_components

    @staticmethod
    def _get_epub(book_epub_path: str):
        epub = EpubUtils.load_epub(book_epub_path)
        return epub

    @staticmethod
    def _make_llm_call(book_epub, prompt: str) -> BookStructuredComponents:
        """
        Given the epub file of the book and the prompt to be used, make a trip to our llm of choice (claude
        for the foreseeable future) using the structured output mode to get a
        `BookStructuredComponents` object containing the books structure
        """

    @staticmethod
    def _get_prompt() -> str:
        """
        Prompt passed to the llm to break down the given book into its components
        :return:
        """
        pass

