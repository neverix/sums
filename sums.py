#%%
import dotenv
dotenv.load_dotenv()
from google import genai
import os
client = genai.Client(api_key=os.environ["GEMINI_KEY"])
# %%
from pathlib import Path
from rich.prompt import Prompt
from rich.console import Console
from rich.table import Table
import time
console = Console()
table = Table(title="Recent EPUB files in ~/Downloads")
table.add_column("Date", justify="right", style="cyan")
table.add_column("Size", justify="right", style="magenta")
table.add_column("Name", style="green")
paths = sorted(Path("~/Downloads").expanduser().glob("*.epub"), key=lambda x: x.stat().st_mtime, reverse=True)[:5]
for path in paths:
    table.add_row(time.ctime(path.stat().st_mtime), f"{path.stat().st_size / 1024:.2f} KiB", path.name)
console.print(table)
picked_path_prefix = Prompt.ask("Pick book")
picked_path = next((path for path in paths if path.name.startswith(picked_path_prefix)), None)
if picked_path is None:
    picked_path = next((path for path in paths if picked_path_prefix in path.name), None)
    if picked_path is None:
        console.print("No matching book found")
        exit()
console.print(f"Selected book: {picked_path}")
#%%
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

def get_text_from_html(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    return soup.get_text()

book = epub.read_epub(picked_path)
chapters = []

for item in book.get_items():
    if item.get_type() == ebooklib.ITEM_DOCUMENT:
        content = item.get_content().decode('utf-8')
        soup = BeautifulSoup(content, 'html.parser')
        title = soup.find('title')
        title_text = title.get_text() if title else None
        chapters.append({
            'id': item.id,
            'title': title_text,
            'content': get_text_from_html(content)
        })
console.print(f"Found {len(chapters)} chapters")
for i, chapter in enumerate(chapters[:5]):
    console.print(f"{i+1}. {chapter['title']}")
    console.print(f" {repr(chapter['content'][:100])}")
if len(chapters) > 5:
    console.print("...")
start_chapter = Prompt.ask("How many chapters to skip?")
if start_chapter:
    start_chapter = int(start_chapter)
    if start_chapter:
        chapters = chapters[start_chapter:]
        console.print("Skipping", start_chapter, "chapters.")
chapters = [
    chapter | (dict(title=f"Chapter {i}") if chapter["title"] is None else {})
    for i, chapter in enumerate(chapters, start=1)
]
# %%
chat = client.chats.create(
    model="gemini-2.5-flash-preview-04-17",
)
prompt = f"""
You are a book summarizer. You will accurately read a book chapter-by-chapter and write summaries of each chapter,
 taking into account important events, small details and the overall plot.
 For each chapter, write a long summary (a few paragraphs) and a short summary (5-10 high-level points, each splitting into 5-15 more detailed points).
 Think before you write the summary and keep the output to the point.
 You will be summarizing the book: {picked_path.name}.
 
First chapter:

# {chapters[0]["title"]}

{chapters[0]["content"]}""".replace("\n\n", "sentinel").replace("\n", "").replace("sentinel", "\n")

all_responses = []

def get_response(chapter, prompt):
    responses = []
    for part in chat.send_message([prompt]).candidates[0].content.parts:
        text = part.text
        responses.append(text)
        console.print(text)
    all_responses.append((chapter, responses))
get_response(0, prompt)

# %%
try:
    for i, chapter in enumerate(chapters[1:], start=1):
        get_response(i, f"# {chapter['title']}\{chapter['content']}")
except KeyboardInterrupt:
    console.print("Interrupting summary...")
#%%
summary_path = Path("summary.md")
summary.write_text("\n\n\n".join(f"# {chapters[i]['title']}\n" + "\n\n".join(responses) for i, responses in all_responses))