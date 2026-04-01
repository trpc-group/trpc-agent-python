# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import time
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.agents import LlmAgent
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
    """创建一个包含多个对话事件的测试会话"""

    # 创建会话
    session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )

    # 添加多个对话事件来模拟长对话
    events = [
        ("user", "你好！我想学习Python编程，你能帮我吗？"), ("assistant", "当然可以！Python是一个很好的编程语言选择。你想从哪个方面开始学习呢？比如基础语法、数据结构、还是特定应用？"),
        ("user", "我想先学习基础语法，然后做一些小项目。"), ("assistant", "很好的学习计划！让我们从Python基础语法开始。首先你需要了解变量、数据类型、控制流等概念。"),
        ("user", "什么是变量？能给我举个例子吗？"),
        ("assistant",
         "变量是用来存储数据的容器。在Python中，你可以这样定义变量：\n\nname = 'Alice'\nage = 25\nis_student = True\n\n变量名可以包含字母、数字和下划线，但不能以数字开头。"
         ), ("user", "明白了！那数据类型有哪些？"),
        ("assistant",
         "Python的主要数据类型包括：\n\n1. 整数 (int): 如 1, 100, -5\n2. 浮点数 (float): 如 3.14, 2.0\n3. 字符串 (str): 如 'hello', \"world\"\n4. 布尔值 (bool): True 或 False\n5. 列表 (list): 如 [1, 2, 3]\n6. 字典 (dict): 如 {'name': 'Alice', 'age': 25}"
         ), ("user", "控制流是什么意思？"),
        ("assistant",
         "控制流是程序执行的顺序控制，主要包括：\n\n1. if-elif-else 条件语句\n2. for 和 while 循环\n3. break 和 continue 语句\n\n例如：\n\nif age >= 18:\n    print('成年人')\nelif age >= 12:\n    print('青少年')\nelse:\n    print('儿童')"
         ), ("user", "这些概念我都理解了，现在我想做一个小项目来练习。"), ("assistant", "太好了！让我们做一个简单的计算器项目。这个项目会用到我们刚才学的所有概念：变量、数据类型、控制流等。"),
        ("user", "好的！请告诉我怎么做这个计算器。"),
        ("assistant",
         "我们来创建一个简单的计算器，支持加减乘除操作：\n\n```python\nwhile True:\n    print('简单计算器')\n    print('1. 加法')\n    print('2. 减法')\n    print('3. 乘法')\n    print('4. 除法')\n    print('5. 退出')\n    \n    choice = input('请选择操作 (1-5): ')\n    \n    if choice == '5':\n        break\n    elif choice in ['1', '2', '3', '4']:\n        num1 = float(input('输入第一个数字: '))\n        num2 = float(input('输入第二个数字: '))\n        \n        if choice == '1':\n            result = num1 + num2\n        elif choice == '2':\n            result = num1 - num2\n        elif choice == '3':\n            result = num1 * num2\n        elif choice == '4':\n            if num2 != 0:\n                result = num1 / num2\n            else:\n                print('错误：除数不能为零')\n                continue\n        \n        print(f'结果: {result}')\n    else:\n        print('无效选择，请重试')\n```"
         ), ("user", "这个计算器看起来不错！我运行了代码，效果很好。现在我想学习更高级的Python概念。"),
        ("assistant",
         "很好！现在我们可以学习一些更高级的Python概念，比如：\n\n1. 函数定义和调用\n2. 类和对象（面向对象编程）\n3. 异常处理\n4. 文件操作\n5. 模块和包\n\n你想从哪个开始？"),
        ("user", "我想先学习函数，因为我觉得这是编程中很重要的概念。"),
        ("assistant",
         "函数确实是编程的核心概念！函数是一段可重用的代码块，可以接受输入（参数）并返回输出。\n\n基本语法：\n```python\ndef function_name(parameters):\n    # 函数体\n    return result\n```\n\n例如：\n```python\ndef greet(name):\n    return f'Hello, {name}!'\n\ndef add(a, b):\n    return a + b\n\ndef calculate_area(length, width):\n    area = length * width\n    return area\n```"
         ), ("user", "我明白了！函数可以让代码更模块化和可重用。现在我想学习面向对象编程。"),
        ("assistant",
         "面向对象编程（OOP）是Python的重要特性！它使用类和对象来组织代码。\n\n类的基本概念：\n```python\nclass Person:\n    def __init__(self, name, age):  # 构造函数\n        self.name = name\n        self.age = age\n    \n    def greet(self):\n        return f'Hello, I am {self.name}'\n    \n    def get_age(self):\n        return self.age\n\n# 创建对象\nperson1 = Person('Alice', 25)\nperson2 = Person('Bob', 30)\n\nprint(person1.greet())  # Hello, I am Alice\nprint(person2.get_age())  # 30\n```"
         ), ("user", "OOP的概念我也理解了！现在我想学习如何处理异常。"),
        ("assistant",
         "异常处理是编写健壮程序的重要部分！Python使用try-except语句来处理异常。\n\n基本语法：\n```python\ntry:\n    # 可能出错的代码\n    result = 10 / 0\nexcept ZeroDivisionError:\n    # 处理特定异常\n    print('错误：除数不能为零')\nexcept Exception as e:\n    # 处理其他异常\n    print(f'发生错误：{e}')\nelse:\n    # 没有异常时执行\n    print('计算成功')\nfinally:\n    # 总是执行\n    print('清理工作')\n```"
         ), ("user", "这些高级概念我都学会了！现在我想做一个更复杂的项目来综合运用这些知识。"),
        ("assistant",
         "太好了！让我们做一个图书管理系统项目，这个项目会用到我们学过的所有概念：\n\n1. 变量和数据类型\n2. 控制流\n3. 函数\n4. 类和对象\n5. 异常处理\n6. 文件操作\n\n这个项目会帮助你巩固所有学过的Python知识。你想开始吗？"
         ), ("user", "是的！请告诉我如何实现这个图书管理系统。"),
        ("assistant",
         "好的！让我们创建一个简单的图书管理系统。这个系统将包含以下功能：\n\n1. 添加图书\n2. 删除图书\n3. 查找图书\n4. 显示所有图书\n5. 保存到文件\n\n我们分步骤来实现，首先创建基本的类结构：\n\n```python\nclass Book:\n    def __init__(self, title, author, isbn, year):\n        self.title = title\n        self.author = author\n        self.isbn = isbn\n        self.year = year\n    \n    def __str__(self):\n        return f'{self.title} by {self.author} ({self.year})'\n\nclass Library:\n    def __init__(self):\n        self.books = []\n    \n    def add_book(self, book):\n        self.books.append(book)\n        print(f'添加图书：{book}')\n    \n    def remove_book(self, isbn):\n        for book in self.books:\n            if book.isbn == isbn:\n                self.books.remove(book)\n                print(f'删除图书：{book}')\n                return\n        print('未找到该图书')\n    \n    def find_book(self, title):\n        for book in self.books:\n            if title.lower() in book.title.lower():\n                print(book)\n        \n    def display_all(self):\n        if not self.books:\n            print('图书馆为空')\n        else:\n            for book in self.books:\n                print(book)\n```"
         ), ("user", "这个图书管理系统的基础结构很好！现在我想学习如何将数据保存到文件中。"),
        ("assistant",
         "文件操作是Python的重要功能！我们可以使用内置的open()函数来读写文件。\n\n让我们为图书管理系统添加文件操作功能：\n\n```python\nimport json\n\nclass Library:\n    def __init__(self, filename='library.json'):\n        self.books = []\n        self.filename = filename\n        self.load_books()\n    \n    def save_books(self):\n        try:\n            with open(self.filename, 'w', encoding='utf-8') as f:\n                books_data = []\n                for book in self.books:\n                    books_data.append({\n                        'title': book.title,\n                        'author': book.author,\n                        'isbn': book.isbn,\n                        'year': book.year\n                    })\n                json.dump(books_data, f, ensure_ascii=False, indent=2)\n            print('图书数据已保存到文件')\n        except Exception as e:\n            print(f'保存文件时出错：{e}')\n    \n    def load_books(self):\n        try:\n            with open(self.filename, 'r', encoding='utf-8') as f:\n                books_data = json.load(f)\n                for book_data in books_data:\n                    book = Book(\n                        book_data['title'],\n                        book_data['author'],\n                        book_data['isbn'],\n                        book_data['year']\n                    )\n                    self.books.append(book)\n            print('图书数据已从文件加载')\n        except FileNotFoundError:\n            print('图书文件不存在，将创建新文件')\n        except Exception as e:\n            print(f'加载文件时出错：{e}')\n```"
         ), ("user", "太棒了！现在我已经学会了Python的基础和高级概念，包括文件操作。我想总结一下我学到的知识。"),
        ("assistant",
         "恭喜你完成了Python编程的学习！让我为你总结一下你掌握的知识：\n\n📚 **Python学习总结**\n\n**基础概念：**\n- 变量和数据类型（int, float, str, bool, list, dict）\n- 控制流（if-elif-else, for, while循环）\n- 基本语法和运算符\n\n**高级概念：**\n- 函数定义和调用\n- 面向对象编程（类和对象）\n- 异常处理（try-except）\n- 文件操作（读写文件）\n\n**实践项目：**\n- 简单计算器\n- 图书管理系统\n\n你已经具备了Python编程的基础能力！接下来你可以：\n1. 学习更多Python库（如numpy, pandas, matplotlib）\n2. 探索Web开发（Django, Flask）\n3. 学习数据科学和机器学习\n4. 参与开源项目\n\n继续保持学习的热情！"
         )
    ]

    # 模拟添加事件到会话
    for i, (author, content) in enumerate(events):
        # 创建事件
        event = Event(
            invocation_id=f"inv_{i:03d}",
            author=author,
            content=Content(parts=[Part.from_text(text=content)], role=author),
            timestamp=time.time() - (len(events) - i) * 60  # 模拟时间间隔
        )

        # 添加事件到会话
        await session_service.append_event(session, event)

    return session


async def summarize_session(session_service: InMemorySessionService, app_name: str, user_id: str, session_id: str):
    """总结会话"""
    # 手动强制创建总结
    print(f"\n--- 开始手动创建总结 ---")
    session = await _create_test_session_with_events(session_service, app_name, user_id, session_id)
    await session_service.summarizer_manager.create_session_summary(session, force=True)
    if session:
        summary = await session_service.summarizer_manager.get_session_summary(session)
        if summary:
            print(f"   - 总结文本: {summary.summary_text[:100]}...")
            print(f"   - 总结时间: {time.ctime(summary.summary_timestamp)}")
            print(f"   - 原始事件数: {summary.original_event_count}")
            print(f"   - 压缩后事件数: {summary.compressed_event_count}")
            print(f"   - 压缩比例: {summary.get_compression_ratio():.1f}%")


SUMMARIZER_COUNT = 3  # 每 summarizer_count 轮对话后执行总结, 如果设置的 summarizer_count 为 3，则每3轮对话后执行总结


def create_summarizer_manager(model: OpenAIModel) -> SummarizerSessionManager:
    """创建SummarizerSessionManager"""
    # 创建总结器
    summarizer = SessionSummarizer(
        model=model,
        # 如果不设置 check_summarizer_functions，默认也会有 set_summarizer_conversation_threshold(100) 这个函数
        # 当 check_summarizer_functions 中的检查函数为为 true，会触发总结, 当存在多个检查函数时，默认采用 AND 逻辑
        # 当检查函数返回 True，自动进行总结, 当检查函数返回 False， 不进行总结
        check_summarizer_functions=[
            set_summarizer_conversation_threshold(SUMMARIZER_COUNT),  # 对话计数检查函数，即每 summarizer_count 轮后执行总结
            # set_summarizer_time_interval_threshold(10), # 时间检查函数，即每10秒需要执行总结
            # set_summarizer_token_threshold(1000), # token检查函数，即每1000个token需要执行总结
            # set_summarizer_important_content_threshold(), # 重要内容检查函数，即根据内容重要度来判断是否需要执行总结
            # set_summarizer_check_functions_by_and( # 组合检查函数, 采用 AND 逻辑， 当所有检查函数都返回 True 时，会触发总结
            #     set_summarizer_conversation_threshold(1),
            #     set_summarizer_time_interval_threshold(10),
            #     set_summarizer_token_threshold(1000),
            #     set_summarizer_important_content_threshold(),
            # ),
            # set_summarizer_check_functions_by_or( # 组合检查函数, 采用 OR 逻辑， 当任意一个检查函数返回 True 时，会触发总结
            #     set_summarizer_conversation_threshold(1),
            #     set_summarizer_time_interval_threshold(10),
            # )
        ],
        max_summary_length=600,  # 保留的总结文本长度, 默认是 1000， 超过该长度显示 ...
        keep_recent_count=4,  # 保留最近多少轮对话, 默认是 10
    )
    # 创建 SummarizerSessionManager
    summarizer_manager = SummarizerSessionManager(
        model=model,
        summarizer=summarizer,
        auto_summarize=True,  # 默认是 True, 如果设置为 False, 则不会自动总结
    )
    return summarizer_manager


async def llm_agent_summarizer():
    """演示LlmAgent与SummarizerSessionManager的集成使用"""
    print("=" * 60)
    print("示例2：LlmAgent + SummarizerSessionManager 集成演示")
    print("=" * 60)
    app_name = "llm_summarizer_manager_demo"

    from agent.agent import root_agent

    summarizer_manager = create_summarizer_manager(root_agent.model)
    session_service = InMemorySessionService(summarizer_manager=summarizer_manager)
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "user_005"
    current_session_id = str(uuid.uuid4())
    print(f"📊 会话信息: {app_name}/{user_id}/{current_session_id}")

    # 演示查询列表
    conversations = [
        "你好！我想学习Python编程，你能帮我吗？",
        "什么是变量？能给我举个例子吗？",
        "我明白了！那数据类型有哪些？",
        "控制流是什么意思？",
        "这些概念我都理解了，现在我想做一个小项目来练习。",
        "好的！请告诉我怎么做这个计算器。",
        "这个计算器看起来不错！我运行了代码，效果很好。现在我想学习更高级的Python概念。",
        "我想先学习函数，因为我觉得这是编程中很重要的概念。",
        "我明白了！函数可以让代码更模块化和可重用。现在我想学习面向对象编程。",
        "OOP的概念我也理解了！现在我想学习如何处理异常。",
        "这些高级概念我都学会了！现在我想做一个更复杂的项目来综合运用这些知识。",
        "是的！请告诉我如何实现这个图书管理系统。",
        "这个图书管理系统的基础结构很好！现在我想学习如何将数据保存到文件中。",
        "太棒了！现在我已经学会了Python的基础和高级概念，包括文件操作。我想总结一下我学到的知识。",
    ]

    print(f"\n💬 开始多轮对话 ({len(conversations)} 轮)...")

    for index, query in enumerate(conversations):
        print(f"\n--- 第 {index+1} 轮对话 ---")
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

        # 每 SUMMARIZER_COUNT 轮对话后检查会话状态
        if index % SUMMARIZER_COUNT == 0:  # summarizer_count 轮对话后应该会触发总结
            if session:
                print(f"\n📊 第 {index+1} 轮后会话状态:")
                summary = await session_service.summarizer_manager.get_session_summary(session)
                print(f"   - 总结文本: {summary.summary_text[:100]}...")
                print(f"   - 原始事件数: {summary.original_event_count}")
                print(f"   - 压缩后事件数: {summary.compressed_event_count}")
                print(f"   - 压缩比例: {summary.get_compression_ratio()}")
        print("\n" + "-" * 40)
    # 测试手动强制创建总结
    await summarize_session(session_service, app_name, user_id, current_session_id)


if __name__ == "__main__":
    asyncio.run(llm_agent_summarizer())
