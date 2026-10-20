"""Mod 开发文档站生成器：把 content/ 下的 Markdown 渲染成静态 HTML。

只用标准库：GitHub Actions 上不用装任何依赖就能构建。

    python3 docs/site/build.py            # 生成到 docs/site/_build/
    python3 docs/site/build.py --serve    # 生成后起个本地预览服务

支持的 Markdown 子集（够写文档，不做通用解析器）：
    标题、段落、无序/有序列表、围栏代码块、表格、引用、分隔线，
    行内的 `代码`、**加粗**、*斜体*、[链接](url)。
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTENT = HERE / 'content'
BUILD = HERE / '_build'
ASSETS = HERE / 'assets'

SITE_TITLE = '战国 Mod 开发文档'
FOOTER = '内容来自 docs/site/content/ 与 research/papers/ · API 参考由 scripts/api_doc.py 生成'

# 章节顺序：目录按这个分组排；没列出的排在后面，按名字排。
SECTION_ORDER = ['开始', '开发流程', 'API 参考', '研究与评测', '附录']


def research_pages() -> list[dict]:
    """直接发布研究正文，避免另存一份后逐渐失去同步。"""
    root = HERE.parent.parent
    source = root / 'research/papers/20261020-long-horizon-wild-ranking'
    specs = [
        ('README.md', '01-长任务野榜', '四模型长任务野榜', '直接评价规划兑现、反省后的改变与目标保持。'),
        ('behavior-review.md', '06-行为一致性评分', '核心行为评分与证据链', 'DeepSeek72、Qwen70、MiniMax47、Gemini40，逐项公开加分、扣分与后续变化。'),
        ('goal-revision-cases.md', '07-目标修订与称臣案例', '目标修订与称臣案例', '158局燕与本局赵：称臣不是扣分项，理由、条件与后续是否摇摆才是。'),
        ('minimax-retrospective.md', '04-MiniMax停局报告', 'MiniMax 第14回合停局报告', '目标让渡、审核循环、同窗对照与一次失望的游戏体验。'),
        ('normalization.md', '05-评测归一化', '评测归一化与宏观口径', '共同时间节点、短期贷款消费、早晚终局与人工停止。'),
        ('observations.md', '02-MiniMax对局观察', 'MiniMax 前9期观察', '保留最初的同阶段统计与案例，后续见第14回合停局报告。'),
        ('excerpts.md', '03-审核原文摘录', '审核原文摘录', '累计指标误读与君主接受错误数字的直接证据。'),
    ]
    urls = {(source / name).resolve(): f'研究与评测/{slug}.html'
            for name, slug, _, _ in specs}
    pages = []
    for order, (name, slug, title, summary) in enumerate(specs, 1):
        path = source / name

        def link(match: re.Match) -> str:
            label, target = match.groups()
            if target.startswith(('http://', 'https://', '#', 'mailto:', '/')):
                return match.group(0)
            relative, marker, anchor = target.partition('#')
            resolved = (path.parent / relative).resolve()
            url = urls.get(resolved)
            if url is None:
                url = 'https://github.com/northwindlight/zhanguo/blob/main/' + resolved.relative_to(root).as_posix()
            return f'[{label}]({url}{marker}{anchor})'

        body = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', link, path.read_text(encoding='utf-8'))
        body += f'\n\n[GitHub 正文与版本记录](https://github.com/northwindlight/zhanguo/blob/main/{path.relative_to(root).as_posix()})\n'
        url = urls[path.resolve()]
        pages.append({'title': title, 'section': '研究与评测', 'summary': summary,
                      'order': order, 'path': Path(url), 'body': body, 'url': url})
    return pages


# ── 前言 ──────────────────────────────────────────────────────────────
def front_matter(text: str) -> tuple[dict, str]:
    """解析开头�� --- --- 之间的前言：key: value。"""
    if not text.startswith('---'):
        return {}, text
    end = text.find('\n---', 3)
    if end < 0:
        return {}, text
    block = text[3:end]
    body = text[end + 4:].lstrip('\n')
    data = {}
    for line in block.splitlines():
        if not line.strip() or ':' not in line:
            continue
        key, value = line.split(':', 1)
        data[key.strip()] = value.strip()
    return data, body


# ── 行内 ──────────────────────────────────────────────────────────────
def inline(text: str) -> str:
    """行内标记 → HTML。文本先转义，再套标记。"""
    placeholders: list[str] = []

    def stash(markup: str) -> str:
        placeholders.append(markup)
        return f'\x00{len(placeholders) - 1}\x00'

    def code(match: re.Match) -> str:
        return stash(f'<code>{html.escape(match.group(1))}</code>')

    def link(match: re.Match) -> str:
        label, target = match.group(1), match.group(2)
        external = target.startswith(('http://', 'https://'))
        attrs = ' target="_blank" rel="noopener"' if external else ''
        return stash(f'<a href="{html.escape(target, quote=True)}"{attrs}>{label}</a>')

    text = re.sub(r'`([^`]+)`', code, text)
    text = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', link, text)
    text = html.escape(text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<![\w*])\*([^*\n]+)\*(?![\w*])', r'<em>\1</em>', text)
    for index, markup in enumerate(placeholders):
        text = text.replace(f'\x00{index}\x00', markup)
    return text


def slug(text: str) -> str:
    """标题 → 锚点 id：中文原样保留，空格与标点变连字符。"""
    cleaned = re.sub(r'[^\w一-鿿\- ]+', '', text.strip().lower())
    return re.sub(r'[\s_]+', '-', cleaned) or 'section'


# ── 块级 ──────────────────────────────────────────────────────────────
def render(markdown: str) -> tuple[str, list[tuple[str, str, int]]]:
    """渲染 Markdown，返回 (HTML, [(锚点, 标题, 层级)])。"""
    lines = markdown.splitlines()
    html_parts: list[str] = []
    headings: list[tuple[str, str, int]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        # 围栏代码块
        if stripped.startswith('```'):
            language = stripped[3:].strip()
            index += 1
            code: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith('```'):
                code.append(lines[index])
                index += 1
            index += 1
            attribute = f' class="language-{html.escape(language)}"' if language else ''
            html_parts.append(f'<pre><code{attribute}>{html.escape(chr(10).join(code))}</code></pre>')
            continue

        # 标题
        heading = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            anchor = slug(title)
            headings.append((anchor, title, level))
            html_parts.append(f'<h{level} id="{html.escape(anchor)}">{inline(title)}</h{level}>')
            index += 1
            continue

        # 分隔线
        if re.match(r'^(-{3,}|\*{3,})$', stripped):
            html_parts.append('<hr>')
            index += 1
            continue

        # 表格
        if stripped.startswith('|') and index + 1 < len(lines) and re.match(r'^\|[\s:|-]+\|$', lines[index + 1].strip()):
            head = [cell.strip() for cell in stripped.strip('|').split('|')]
            index += 2
            rows = []
            while index < len(lines) and lines[index].strip().startswith('|'):
                rows.append([cell.strip() for cell in lines[index].strip().strip('|').split('|')])
                index += 1
            head_html = ''.join(f'<th>{inline(cell)}</th>' for cell in head)
            body_html = ''.join('<tr>' + ''.join(f'<td>{inline(cell)}</td>' for cell in row) + '</tr>'
                                for row in rows)
            html_parts.append(f'<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>')
            continue

        # 引用
        if stripped.startswith('>'):
            block = []
            while index < len(lines) and lines[index].strip().startswith('>'):
                block.append(lines[index].strip().lstrip('>').strip())
                index += 1
            html_parts.append(f'<blockquote><p>{inline(" ".join(block))}</p></blockquote>')
            continue

        # 列表
        if re.match(r'^([-*+]|\d+\.)\s+', stripped):
            items, index = read_list(lines, index)
            html_parts.append(items)
            continue

        # 段落
        block = []
        while index < len(lines):
            current = lines[index].strip()
            if not current or current.startswith(('#', '>', '|', '```')) \
                    or re.match(r'^([-*+]|\d+\.)\s+', current) \
                    or re.match(r'^(-{3,}|\*{3,})$', current):
                break
            block.append(current)
            index += 1
        html_parts.append(f'<p>{inline(" ".join(block))}</p>')

    return '\n'.join(html_parts), headings


def read_list(lines: list[str], index: int) -> tuple[str, int]:
    """读一个列表（含两级缩进），返回 (HTML, 下一行号)。"""
    ordered = bool(re.match(r'^\d+\.', lines[index].strip()))
    items: list[tuple[int, list[str]]] = []
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            # 空行后若还是列表项就继续，否则收尾
            look = index + 1
            while look < len(lines) and not lines[look].strip():
                look += 1
            if look < len(lines) and re.match(r'^([-*+]|\d+\.)\s+', lines[look].strip()):
                index = look
                continue
            break
        match = re.match(r'^([-*+]|\d+\.)\s+(.*)$', stripped)
        if match:
            indent = len(raw) - len(raw.lstrip(' '))
            items.append((indent, [match.group(2)]))
            index += 1
            continue
        if items and raw.startswith(('  ', '\t')):
            items[-1][1].append(stripped)
            index += 1
            continue
        break
    tag = 'ol' if ordered else 'ul'
    parts = [f'<{tag}>']
    for indent, text_lines in items:
        content = inline(' '.join(text_lines))
        if indent >= 2 and len(parts) > 1:
            parts[-1] = parts[-1].replace('</li>', '</li>')  # 结构保持简单：缩进项降级为同级
        parts.append(f'<li>{content}</li>')
    parts.append(f'</{tag}>')
    return '\n'.join(parts), index


# ── 页面 ──────────────────────────────────────────────────────────────
def strip_markdown(markdown: str) -> str:
    """去掉标记，得到可搜索的纯文本。"""
    text = re.sub(r'```.*?```', ' ', markdown, flags=re.S)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'[*#>|]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def collect() -> list[dict]:
    """读全部内容页，按分组与顺序整理。"""
    pages = []
    for path in sorted(CONTENT.rglob('*.md')):
        if path.name == 'index.md':
            continue
        data, body = front_matter(path.read_text(encoding='utf-8'))
        relative = path.relative_to(CONTENT)
        pages.append({
            'title': data.get('title') or path.stem,
            'section': data.get('section') or path.parent.name,
            'summary': data.get('summary', ''),
            'order': int(data.get('order', 99)),
            'path': relative,
            'body': body,
            'url': str(relative.with_suffix('.html')).replace('\\', '/'),
        })
    pages.extend(research_pages())
    pages.sort(key=lambda row: (SECTION_ORDER.index(row['section'])
                                 if row['section'] in SECTION_ORDER else len(SECTION_ORDER),
                                 row['order'], row['title']))
    return pages


def rewrite_links(body: str, prefix: str) -> str:
    """正文里的站内链接按**站点根**写，构建时按当前页面深度改写。

    作者写 `开发流程/03-暴露给模型.html`，构建器负责让它在子目录页里也跳对。
    """
    def fix(match: re.Match) -> str:
        attribute, target = match.group(1), match.group(2)
        if target.startswith(('http://', 'https://', '#', 'mailto:', '/')):
            return match.group(0)
        if target.startswith('../'):
            return match.group(0)
        return f'{attribute}="{prefix}{target}"'

    return re.sub(r'\b(href|src)="([^"]+)"', fix, body)


def page_html(page: dict, nav: str, prefix: str, active: str) -> str:
    body, headings = render(page['body'])
    # 标题已经由前言渲染成 doc-title，正文里开头那个 h1 是重复的，去掉。
    body = re.sub(r'^\s*<h1 id="[^"]*">.*?</h1>\s*', '', body, count=1, flags=re.S)
    body = rewrite_links(body, prefix)
    toc = ''
    entries = [(anchor, title, level) for anchor, title, level in headings if level in (2, 3)]
    if entries:
        items = ''.join(f'<li class="toc-level-{level}"><a href="#{html.escape(anchor)}">{inline(title)}</a></li>'
                        for anchor, title, level in entries)
        toc = f'<nav class="toc"><h2 class="toc-title">本页目录</h2><ul>{items}</ul></nav>'
    title = html.escape(page['title'])
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · {SITE_TITLE}</title>
<link rel="stylesheet" href="{prefix}assets/style.css">
</head>
<body>
<header class="topbar">
  <a class="brand" href="{prefix}index.html">{SITE_TITLE}</a>
  <button class="nav-toggle" id="nav-toggle" aria-label="切换目录" aria-expanded="false">☰</button>
  <div class="search" role="search">
    <input type="search" id="search-input" placeholder="搜索 API、命令、字段…" autocomplete="off" aria-label="搜索文档">
    <div class="search-results" id="search-results" hidden></div>
  </div>
</header>
<div class="layout">
  <nav class="sidebar" id="sidebar">
{nav}
  </nav>
  <main class="main">
    <article class="doc">
      <h1 class="doc-title">{title}</h1>
{f'      <p class="doc-summary">{html.escape(page["summary"])}</p>' if page['summary'] else ''}
{toc}
{body}
    </article>
    <footer class="doc-footer">{FOOTER}</footer>
  </main>
</div>
<script src="{prefix}assets/search-index.js"></script>
<script src="{prefix}assets/app.js"></script>
</body>
</html>
"""


def nav_html(pages: list[dict], active: str, prefix: str = '') -> str:
    """侧栏导航。链接按**当前页面的深度**加前缀，子目录页也能跳对。"""
    groups: dict[str, list[dict]] = {}
    for page in pages:
        groups.setdefault(page['section'], []).append(page)
    parts = ['<ul class="nav-tree">']
    for section in sorted(groups, key=lambda name: (SECTION_ORDER.index(name)
                                                   if name in SECTION_ORDER else len(SECTION_ORDER), name)):
        parts.append(f'<li class="nav-group"><span class="nav-group-title">{html.escape(section)}</span><ul>')
        for page in groups[section]:
            current = ' current' if page['url'] == active else ''
            parts.append(f'<li><a class="nav-link{current}" href="{prefix}{page["url"]}">'
                         f'{html.escape(page["title"])}</a></li>')
        parts.append('</ul></li>')
    parts.append('</ul>')
    return '\n'.join(parts)


def build() -> None:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)
    pages = collect()
    index_path = CONTENT / 'index.md'
    index_data, index_body = front_matter(index_path.read_text(encoding='utf-8')) if index_path.is_file() else ({}, '')

    entries = []
    for page in pages:
        prefix = '../' if '/' in page['url'] else ''
        nav = nav_html(pages, page['url'], prefix)
        target = BUILD / page['url']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page_html(page, nav, prefix, page['url']), encoding='utf-8')
        entries.append({
            'title': page['title'], 'section': page['section'], 'path': page['url'],
            'summary': page['summary'], 'text': strip_markdown(page['body'])[:20000],
        })

    # 首页
    index_page = {
        'title': index_data.get('title', '战国 Mod 开发文档'),
        'summary': index_data.get('summary', ''),
        'body': index_body,
        'url': 'index.html',
    }
    nav = nav_html(pages, 'index.html')
    (BUILD / 'index.html').write_text(page_html(index_page, nav, '', 'index.html'), encoding='utf-8')
    entries.append({
        'title': index_page['title'], 'section': '首页', 'path': 'index.html',
        'summary': index_page['summary'], 'text': strip_markdown(index_body)[:20000],
    })

    # 静态资源与搜索索引
    (BUILD / 'assets').mkdir(parents=True, exist_ok=True)
    for name in ('style.css', 'app.js'):
        source = ASSETS / name
        if source.is_file():
            shutil.copy(source, BUILD / 'assets' / name)
    (BUILD / 'assets' / 'search-index.js').write_text(
        'window.DOC_SEARCH_INDEX = ' + json.dumps({'pages': entries}, ensure_ascii=False) + ';\n',
        encoding='utf-8')
    print(f'生成 {len(pages) + 1} 个页面 → {BUILD}')
    broken = check_links()
    if broken:
        print('站内链接有断链：')
        for row in broken:
            print('  ' + row)
        return 1
    print('站内链接自检通过。')


def check_links() -> list[str]:
    """站内链接自检：所有相对链接都要指向真实存在的文件。"""
    broken = []
    for page in sorted(BUILD.rglob('*.html')):
        text = page.read_text(encoding='utf-8')
        for target in re.findall(r'(?:href|src)="([^"]+)"', text):
            if target.startswith(('http://', 'https://', '#', 'mailto:')):
                continue
            path, _, anchor = target.partition('#')
            if not path:
                continue
            resolved = (page.parent / path).resolve()
            if not resolved.exists():
                broken.append(f'{page.relative_to(BUILD)} → {target}')
    return broken


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true', help='生成后起本地预览服务')
    args = parser.parse_args()
    build()
    if check_links():
        return 1
    if args.serve:
        import http.server
        import socketserver
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(BUILD), **k)  # noqa: E731
        with socketserver.TCPServer(('127.0.0.1', 8765), handler) as server:
            print('预览：http://127.0.0.1:8765/')
            server.serve_forever()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
