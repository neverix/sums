import argparse
import os
import time
from pathlib import Path

import dotenv
from bs4 import BeautifulSoup
from ebooklib import epub
import ebooklib
from google import genai
from google.genai import types
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

def main():
    parser = argparse.ArgumentParser(description='Summarize an EPUB book using Gemini')
    parser.add_argument('--source', type=str, default='~/Downloads', 
                        help='Directory to search for EPUB files')
    parser.add_argument('--path', type=str, help='Direct path to EPUB file')
    parser.add_argument('--skip', type=int, help='Number of chapters to skip')
    parser.add_argument('--model', type=str, default='gemini-2.5-flash-preview-04-17',
                        help='Gemini model to use')
    parser.add_argument('--output', type=str, default='summary.md',
                        help='Output file path')
    args = parser.parse_args()
    
    dotenv.load_dotenv()
    console = Console()
    
    # Initialize Gemini client
    client = genai.Client(api_key=os.environ["GEMINI_KEY"])
    config = types.GenerateContentConfig()
    
    picked_path = None
    if args.path:
        picked_path = Path(args.path).expanduser()
        if not picked_path.exists() or picked_path.suffix != '.epub':
            console.print(f"Invalid EPUB file: {picked_path}")
            exit()
        console.print(f"Using specified book: {picked_path}")
    else:
        # Display recent EPUB files
        table = Table(title=f"Recent EPUB files in {args.source}")
        table.add_column("Date", justify="right", style="cyan")
        table.add_column("Size", justify="right", style="magenta")
        table.add_column("Name", style="green")
        
        paths = sorted(Path(args.source).expanduser().glob("*.epub"), 
                      key=lambda x: x.stat().st_mtime, reverse=True)[:5]
        
        for path in paths:
            table.add_row(time.ctime(path.stat().st_mtime), 
                         f"{path.stat().st_size / 1024:.2f} KiB", path.name)
        console.print(table)
        
        # Select book
        picked_path_prefix = Prompt.ask("Pick book")
        picked_path = next((path for path in paths if path.name.startswith(picked_path_prefix)), None)
        if picked_path is None:
            picked_path = next((path for path in paths if picked_path_prefix in path.name), None)
            if picked_path is None:
                console.print("No matching book found")
                exit()
        console.print(f"Selected book: {picked_path}")
    
    # Parse book
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
                'content': soup.get_text()
            })
    
    console.print(f"Found {len(chapters)} chapters")
    for i, chapter in enumerate(chapters[:5]):
        console.print(f"{i+1}. {chapter['title']}")
        console.print(f" {repr(chapter['content'][:100])}")
    if len(chapters) > 5:
        console.print("...")
    
    # Skip chapters if needed
    start_chapter = args.skip
    if start_chapter is None:
        start_chapter = Prompt.ask("How many chapters to skip?")
        if start_chapter:
            start_chapter = int(start_chapter)
    if start_chapter:
        chapters = chapters[start_chapter:]
        console.print("Skipping", start_chapter, "chapters.")
    
    # Ensure all chapters have titles
    chapters = [
        chapter | (dict(title=f"Chapter {i}") if chapter["title"] is None else {})
        for i, chapter in enumerate(chapters, start=1)
    ]
    
    # Initialize chat and process chapters
    chat = client.chats.create(
        model=args.model,
        config=config,
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

    def get_response(chat, chapter, prompt, console, all_responses):
        responses = []
        for part in chat.send_message([prompt]).candidates[0].content.parts:
            text = part.text
            responses.append(text)
            console.print(text)
        all_responses.append((chapter, responses))
    
    get_response(chat, 0, prompt, console, all_responses)
    
    try:
        for i, chapter in enumerate(chapters[1:], start=1):
            get_response(chat, i, f"# {chapter['title']}\n{chapter['content']}", console, all_responses)
    except KeyboardInterrupt:
        console.print("Interrupting summary...")
    
    # Save summary
    summary_path = Path(args.output)
    summary = "\n\n\n".join(f"# {chapters[i]['title']}\n" + "\n\n".join(responses) 
                           for i, responses in all_responses)
    summary_path.write_text(summary)
    console.print(f"Summary saved to {summary_path.absolute()}")

if __name__ == "__main__":
    main()