from src.services.book_component_breakdown_service import BookComponentBreakdownService


class BookComponentBreakdownWorkflow:
    """
    This workflow orchestrates the initial ingestion of a book, managing its breakdown
    into it's constituent components.

    The definition of done for this workflow is:
        - ensuring we have, in corresponding postgres tables, entries for each type
          of component the given book (indicated by its file path) has
        - ensuring each component entry is well-formed and matches the corresponding
          pydantic model
    """

    def __init__(self, book_path: str):
        self.book_path = book_path

    def run_workflow(self):
        # 1. Get the actual book components
        book_components = BookComponentBreakdownService.get_book_components(self.book_path)

        # 2. Write results to postgres
        # 3. Maybe do some validation? idk

        return