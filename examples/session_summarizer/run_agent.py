# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import asyncio
import time
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.sessions import SummarizerSessionManager
from trpc_agent_sdk.sessions import set_summarizer_conversation_threshold
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()


async def _create_test_session_with_events(session_service: InMemorySessionService, app_name: str, user_id: str,
                                           session_id: str):
    """Create a test session with multiple dialogue events."""

    # Create session
    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )

    # Append many dialogue events to simulate a long conversation
    events = [
        ("user", "Hello! I want to learn Python programming. Can you help me?"),
        ("assistant",
         "Of course! Python is a great choice. Where would you like to start—basic syntax, data structures, or a specific application?"
         ),
        ("user", "I want to learn basic syntax first, then do small projects."),
        ("assistant",
         "Good plan! Let's start with Python basics: variables, data types, control flow, and related ideas."),
        ("user", "What is a variable? Can you give an example?"),
        ("assistant",
         "A variable holds data. In Python you can write:\n\nname = 'Alice'\nage = 25\nis_student = True\n\nNames may contain letters, digits, and underscores, but cannot start with a digit."
         ),
        ("user", "Got it! What data types are there?"),
        ("assistant",
         "Main Python types include:\n\n1. int: e.g. 1, 100, -5\n2. float: e.g. 3.14, 2.0\n3. str: e.g. 'hello', \"world\"\n4. bool: True or False\n5. list: e.g. [1, 2, 3]\n6. dict: e.g. {'name': 'Alice', 'age': 25}"
         ),
        ("user", "What does control flow mean?"),
        ("assistant",
         "Control flow steers execution order:\n\n1. if-elif-else\n2. for and while loops\n3. break and continue\n\nExample:\n\nif age >= 18:\n    print('adult')\nelif age >= 12:\n    print('teen')\nelse:\n    print('child')"
         ),
        ("user", "I understand those ideas. I'd like a small project to practice."),
        ("assistant", "Great! Let's build a simple calculator using variables, types, and control flow."),
        ("user", "OK! How do I build this calculator?"),
        ("assistant",
         "Here is a simple calculator with add, subtract, multiply, divide:\n\n```python\nwhile True:\n    print('Simple calculator')\n    print('1. Add')\n    print('2. Subtract')\n    print('3. Multiply')\n    print('4. Divide')\n    print('5. Quit')\n    \n    choice = input('Choose an operation (1-5): ')\n    \n    if choice == '5':\n        break\n    elif choice in ['1', '2', '3', '4']:\n        num1 = float(input('First number: '))\n        num2 = float(input('Second number: '))\n        \n        if choice == '1':\n            result = num1 + num2\n        elif choice == '2':\n            result = num1 - num2\n        elif choice == '3':\n            result = num1 * num2\n        elif choice == '4':\n            if num2 != 0:\n                result = num1 / num2\n            else:\n                print('Error: division by zero')\n                continue\n        \n        print(f'Result: {result}')\n    else:\n        print('Invalid choice, try again')\n```"
         ),
        ("user", "The calculator looks good—I ran it successfully. I'd like to learn more advanced Python."),
        ("assistant",
         "Nice! Next topics could include:\n\n1. Defining and calling functions\n2. Classes and objects (OOP)\n3. Exception handling\n4. File I/O\n5. Modules and packages\n\nWhich would you like first?"
         ),
        ("user", "I'd like to start with functions—I think they're central to programming."),
        ("assistant",
         "Functions are core! A function is a reusable block that takes inputs (parameters) and can return outputs.\n\nBasic syntax:\n```python\ndef function_name(parameters):\n    # function body\n    return result\n```\n\nExamples:\n```python\ndef greet(name):\n    return f'Hello, {name}!'\n\ndef add(a, b):\n    return a + b\n\ndef calculate_area(length, width):\n    area = length * width\n    return area\n```"
         ),
        ("user", "I see—functions make code modular and reusable. I'd like to learn OOP next."),
        ("assistant",
         "Object-oriented programming (OOP) is a major Python feature; classes and objects organize code.\n\nBasic class idea:\n```python\nclass Person:\n    def __init__(self, name, age):  # constructor\n        self.name = name\n        self.age = age\n    \n    def greet(self):\n        return f'Hello, I am {self.name}'\n    \n    def get_age(self):\n        return self.age\n\n# create instances\nperson1 = Person('Alice', 25)\nperson2 = Person('Bob', 30)\n\nprint(person1.greet())  # Hello, I am Alice\nprint(person2.get_age())  # 30\n```"
         ),
        ("user", "I get OOP now. I'd like to learn exception handling."),
        ("assistant",
         "Exception handling helps you write robust programs. Python uses try-except.\n\nBasic pattern:\n```python\ntry:\n    # code that might fail\n    result = 10 / 0\nexcept ZeroDivisionError:\n    # handle a specific error\n    print('Error: division by zero')\nexcept Exception as e:\n    # handle other errors\n    print(f'An error occurred: {e}')\nelse:\n    # runs when no exception\n    print('Computation succeeded')\nfinally:\n    # always runs\n    print('Cleanup')\n```"
         ),
        ("user", "I've learned these advanced topics. I'd like a bigger project that ties them together."),
        ("assistant",
         "Excellent! Let's build a small library (book) management app using:\n\n1. Variables and types\n2. Control flow\n3. Functions\n4. Classes and objects\n5. Exception handling\n6. File I/O\n\nIt will reinforce everything so far. Ready to start?"
         ),
        ("user", "Yes! How do I implement this library system?"),
        ("assistant",
         "Here is a simple design with:\n\n1. Add a book\n2. Remove a book\n3. Find a book\n4. List all books\n5. Save to file\n\nStep one—basic classes:\n\n```python\nclass Book:\n    def __init__(self, title, author, isbn, year):\n        self.title = title\n        self.author = author\n        self.isbn = isbn\n        self.year = year\n    \n    def __str__(self):\n        return f'{self.title} by {self.author} ({self.year})'\n\nclass Library:\n    def __init__(self):\n        self.books = []\n    \n    def add_book(self, book):\n        self.books.append(book)\n        print(f'Added book: {book}')\n    \n    def remove_book(self, isbn):\n        for book in self.books:\n            if book.isbn == isbn:\n                self.books.remove(book)\n                print(f'Removed book: {book}')\n                return\n        print('Book not found')\n    \n    def find_book(self, title):\n        for book in self.books:\n            if title.lower() in book.title.lower():\n                print(book)\n        \n    def display_all(self):\n        if not self.books:\n            print('Library is empty')\n        else:\n            for book in self.books:\n                print(book)\n```"
         ),
        ("user", "The structure looks good. How do I persist data to files?"),
        ("assistant",
         "File I/O is essential. Use the built-in open() for read/write.\n\nExtend the library with JSON persistence:\n\n```python\nimport json\n\nclass Library:\n    def __init__(self, filename='library.json'):\n        self.books = []\n        self.filename = filename\n        self.load_books()\n    \n    def save_books(self):\n        try:\n            with open(self.filename, 'w', encoding='utf-8') as f:\n                books_data = []\n                for book in self.books:\n                    books_data.append({\n                        'title': book.title,\n                        'author': book.author,\n                        'isbn': book.isbn,\n                        'year': book.year\n                    })\n                json.dump(books_data, f, ensure_ascii=False, indent=2)\n            print('Book data saved to file')\n        except Exception as e:\n            print(f'Error saving file: {e}')\n    \n    def load_books(self):\n        try:\n            with open(self.filename, 'r', encoding='utf-8') as f:\n                books_data = json.load(f)\n                for book_data in books_data:\n                    book = Book(\n                        book_data['title'],\n                        book_data['author'],\n                        book_data['isbn'],\n                        book_data['year']\n                    )\n                    self.books.append(book)\n            print('Book data loaded from file')\n        except FileNotFoundError:\n            print('Book file not found; a new file will be created')\n        except Exception as e:\n            print(f'Error loading file: {e}')\n```"
         ),
        ("user", "Great! I've covered basics and advanced topics including files. I'd like a recap of what I learned."),
        ("assistant",
         "Congratulations on finishing this Python tour! Summary:\n\n📚 **Python recap**\n\n**Basics:**\n- Variables and types (int, float, str, bool, list, dict)\n- Control flow (if-elif-else, for, while)\n- Core syntax and operators\n\n**Advanced:**\n- Defining and calling functions\n- OOP (classes and objects)\n- Exceptions (try-except)\n- File I/O\n\n**Projects:**\n- Simple calculator\n- Library manager\n\nYou have a solid foundation. Next steps:\n1. Explore libraries (numpy, pandas, matplotlib)\n2. Try web development (Django, Flask)\n3. Data science and ML\n4. Contribute to open source\n\nKeep learning!"
         ),
    ]

    # Iterate and append events to the session
    for i, (author, content) in enumerate(events):
        # Create event
        event = Event(
            invocation_id=f"inv_{i:03d}",
            author=author,
            content=Content(parts=[Part.from_text(text=content)], role=author),
            timestamp=time.time() - (len(events) - i) * 60  # Simulate time gaps
        )

        # Append event to session
        await session_service.append_event(session, event)

    return session


async def summarize_session(session_service: InMemorySessionService, app_name: str, user_id: str, session_id: str):
    """Summarize a session."""
    # Manually force summary creation
    print("\n--- Manual summary creation ---")
    session = await _create_test_session_with_events(session_service, app_name, user_id, session_id)
    await session_service.summarizer_manager.create_session_summary(session, force=True)
    if session:
        summary = await session_service.summarizer_manager.get_session_summary(session)
        if summary:
            print(f"   - Summary text: {summary.summary_text[:100]}...")
            print(f"   - Summary time: {time.ctime(summary.summary_timestamp)}")
            print(f"   - Original event count: {summary.original_event_count}")
            print(f"   - Compressed event count: {summary.compressed_event_count}")
            print(f"   - Compression ratio: {summary.get_compression_ratio():.1f}%")


SUMMARIZER_COUNT = 2  # Keep the example short: summarize after a couple of turns.


def create_summarizer_manager(model: OpenAIModel) -> SummarizerSessionManager:
    """Create a SummarizerSessionManager."""
    # Create summarizer
    summarizer = SessionSummarizer(
        model=model,
        # If check_summarizer_functions is omitted, set_summarizer_conversation_threshold(100) still applies by default
        # When any check in check_summarizer_functions is true, summarization runs; multiple checks use AND by default
        # True => auto summarize; False => skip
        check_summarizer_functions=[
            set_summarizer_conversation_threshold(SUMMARIZER_COUNT),  # Turn-count check: every SUMMARIZER_COUNT turns
            # set_summarizer_time_interval_threshold(10),  # Time: summarize every 10 seconds
            # set_summarizer_token_threshold(1000),  # Tokens: every 1000 tokens
            # set_summarizer_important_content_threshold(),  # Importance-based check
            # set_summarizer_check_functions_by_and(  # Combine with AND: all must be True
            #     set_summarizer_conversation_threshold(1),
            #     set_summarizer_time_interval_threshold(10),
            #     set_summarizer_token_threshold(1000),
            #     set_summarizer_important_content_threshold(),
            # ),
            # set_summarizer_check_functions_by_or(  # Combine with OR: any True triggers
            #     set_summarizer_conversation_threshold(1),
            #     set_summarizer_time_interval_threshold(10),
            # )
        ],
        max_summary_length=300,  # Max summary length kept; default 1000; beyond shows ...
        keep_recent_count=2,  # Keep only the latest turns so compression is easy to observe.
    )
    # Create SummarizerSessionManager
    summarizer_manager = SummarizerSessionManager(
        model=model,
        summarizer=summarizer,
        auto_summarize=True,  # Default True; False disables automatic summarization
    )
    return summarizer_manager


async def llm_agent_summarizer():
    """Demo LlmAgent integrated with SummarizerSessionManager."""
    print("=" * 60)
    print("Example: LlmAgent + SummarizerSessionManager demo")
    print("=" * 60)
    app_name = "llm_summarizer_manager_demo"

    from agent.agent import root_agent

    summarizer_manager = create_summarizer_manager(root_agent.model)
    session_service = InMemorySessionService(summarizer_manager=summarizer_manager)
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "user_005"
    current_session_id = str(uuid.uuid4())
    print(f"📊 Session: {app_name}/{user_id}/{current_session_id}")

    # Short demo conversation.  Four turns are enough to trigger automatic
    # summarization while keeping the example quick to run.
    conversations = [
        "Hello! I want to learn Python programming. Can you help me?",
        "What is a variable? Can you give an example?",
        "Please give me a tiny calculator example.",
        "Can you recap what I learned so far?",
    ]

    print(f"\n💬 Multi-turn dialogue ({len(conversations)} turns)...")

    for index, query in enumerate(conversations):
        print(f"\n--- Turn {index + 1} ---")
        session = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=current_session_id)
        user_content = Content(parts=[Part.from_text(text=query)])
        async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
            # Check if event.content exists
            if not event.content or not event.content.parts:
                continue

            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                # Skip the reasoning part; the output is already generated when partial=True
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")
                # Uncomment to get the full text output of the LLM
                # elif part.text:
                #     print(f"\n✅ {part.text}")

        # Inspect the summary after the threshold cadence.
        if (index + 1) % SUMMARIZER_COUNT == 0 and session:
            print(f"\n📊 Session state after turn {index + 1}:")
            summary = await session_service.summarizer_manager.get_session_summary(session)
            if summary:
                print(f"   - Summary text: {summary.summary_text[:100]}...")
                print(f"   - Original event count: {summary.original_event_count}")
                print(f"   - Compressed event count: {summary.compressed_event_count}")
                print(f"   - Compression ratio: {summary.get_compression_ratio()}")
            else:
                print("   - Summary not created yet.")
        print("\n" + "-" * 40)


if __name__ == "__main__":
    asyncio.run(llm_agent_summarizer())
