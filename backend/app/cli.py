import asyncio
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from app.core.database import init_db
from app.core.config import load_config, save_config, ensure_dirs

console = Console()


@click.group()
def cli():
    """Chat Analyzer — 个人聊天记录智能分析系统"""
    pass


@cli.command()
def init():
    """初始化数据库和配置"""
    ensure_dirs()
    asyncio.run(init_db())
    console.print("[green]✓ 数据库初始化完成[/green]")
    console.print(f"  数据目录: ~/.chat-analyzer/")
    console.print(f"  LLM 引擎: Claude Code (claude -p)")


@cli.command()
@click.option("--new-only", is_flag=True, help="仅同步新消息")
def sync(new_only: bool):
    """同步微信聊天数据"""
    from app.services.sync.wechat import sync_sessions, sync_new_messages

    console.print("[bold]正在同步微信数据...[/bold]")

    try:
        if new_only:
            count = asyncio.run(sync_new_messages())
        else:
            count = asyncio.run(sync_sessions())

        if count > 0:
            console.print(f"[green]✓ 同步完成，新增 {count} 条消息[/green]")
        else:
            console.print("[dim]没有新消息[/dim]")
    except Exception as e:
        console.print(f"[red]✗ 同步失败: {e}[/red]")


@cli.command()
@click.argument("path", type=click.Path(exists=True))
def import_qq(path: str):
    """导入 QQ 聊天记录（qq-chat-exporter 导出的 JSON 文件或目录）"""
    from app.services.sync.qq import import_qq_json, import_qq_dir

    console.print("[bold]正在导入 QQ 数据...[/bold]")

    try:
        if Path(path).is_dir():
            result = asyncio.run(import_qq_dir(path))
        else:
            result = asyncio.run(import_qq_json(path))

        files_info = f", {result.get('files', 0)} 个文件" if "files" in result else ""
        console.print(
            f"[green]✓ 导入完成: {result['imported']}/{result['total']} 条消息"
            f" ({result.get('chats', 1)} 个聊天){files_info}[/green]"
        )
    except Exception as e:
        console.print(f"[red]✗ 导入失败: {e}[/red]")


@cli.command()
@click.argument("path", type=click.Path(exists=True))
def import_telegram(path: str):
    """导入 Telegram 聊天记录（官方导出的 result.json 或目录）"""
    from app.services.sync.telegram import import_telegram_json, import_telegram_dir

    console.print("[bold]正在导入 Telegram 数据...[/bold]")

    try:
        if Path(path).is_dir():
            result = asyncio.run(import_telegram_dir(path))
        else:
            result = asyncio.run(import_telegram_json(path))

        files_info = f", {result.get('files', 0)} 个文件" if "files" in result else ""
        console.print(
            f"[green]✓ 导入完成: {result['imported']}/{result['total']} 条消息"
            f" ({result.get('chats', 1)} 个聊天){files_info}[/green]"
        )
    except Exception as e:
        console.print(f"[red]✗ 导入失败: {e}[/red]")


@cli.command("list-chats")
def list_chats():
    """列出所有已导入的聊天"""
    import aiosqlite
    from app.core.database import DB_PATH

    async def _run():
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """SELECT platform, chat_id, chat_name, chat_type,
                          COUNT(*) as msg_count,
                          MIN(timestamp) as earliest,
                          MAX(timestamp) as latest
                   FROM messages
                   GROUP BY platform, chat_id
                   ORDER BY latest DESC"""
            )

        if not rows:
            console.print("[dim]数据库中暂无聊天记录[/dim]")
            return

        cfg = load_config()
        filtered = cfg.chat_filter.chats

        table = Table(title="已导入聊天")
        table.add_column("平台", width=10)
        table.add_column("名称", width=20)
        table.add_column("类型", width=10)
        table.add_column("消息数", width=8)
        table.add_column("时间范围", width=40)
        table.add_column("筛选", width=8)

        for r in rows:
            r = dict(r)
            name = r["chat_name"] or r["chat_id"]
            is_filtered = name in filtered
            filter_tag = (
                "[yellow]跳过[/yellow]" if is_filtered and cfg.chat_filter.mode == "blacklist"
                else "[green]启用[/green]" if not is_filtered and cfg.chat_filter.mode == "blacklist"
                else "[green]启用[/green]" if is_filtered and cfg.chat_filter.mode == "whitelist"
                else "[dim]跳过[/dim]"
            )
            table.add_row(
                r["platform"],
                name[:20],
                r.get("chat_type", "-"),
                str(r["msg_count"]),
                f"{str(r['earliest'])[:10]} ~ {str(r['latest'])[:10]}",
                filter_tag,
            )

        console.print(table)
        console.print(f"\n筛选模式: [bold]{cfg.chat_filter.mode}[/bold] | 筛选列表: {filtered or '(空)'}")

    asyncio.run(_run())


@cli.command()
@click.option("--chat", default=None, help="指定聊天名称")
@click.option("--since", default=None, help="起始时间 (如 2026-04-01 或 2026-04-01T10:00)")
@click.option("--until", default=None, help="结束时间")
@click.option("--limit", default=100, help="分析消息数量上限")
def analyze(chat: str | None, since: str | None, until: str | None, limit: int):
    """分析消息（预过滤 + Claude Code 分类）"""
    import aiosqlite
    from app.core.database import DB_PATH
    from app.services.analyzer import AnalyzerService, TokenBudgetExceeded

    async def _run():
        await init_db()
        svc = AnalyzerService()
        cfg = load_config()

        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row

            conditions = []
            params: list = []

            # Chat filter from config
            if chat:
                conditions.append("chat_name = ?")
                params.append(chat)
            else:
                chat_filter = cfg.chat_filter
                if chat_filter.chats:
                    placeholders = ",".join("?" * len(chat_filter.chats))
                    if chat_filter.mode == "whitelist":
                        conditions.append(f"chat_name IN ({placeholders})")
                    else:  # blacklist
                        conditions.append(f"chat_name NOT IN ({placeholders})")
                    params.extend(chat_filter.chats)

            # Time range filter
            if since:
                conditions.append("timestamp >= ?")
                params.append(since)
            if until:
                conditions.append("timestamp <= ?")
                params.append(until)

            where = " AND ".join(conditions)
            sql = f"""SELECT id, chat_id, chat_name, sender_name, content, msg_type, timestamp
                      FROM messages {'WHERE ' + where if where else ''}
                      ORDER BY timestamp DESC LIMIT ?"""
            params.append(limit)
            rows = await db.execute_fetchall(sql, params)

        if not rows:
            console.print("[dim]没有可分析的消息（检查群聊筛选和时间范围设置）[/dim]")
            return

        messages = [dict(r) for r in rows]
        time_info = f" ({since} ~ {until})" if since or until else ""
        console.print(f"[bold]分析 {len(messages)} 条消息{time_info} (via claude -p)...[/bold]")

        # Pre-filter stats
        from app.services.pre_filter import filter_messages

        passed, noise = filter_messages(messages)
        console.print(f"  预过滤: {len(noise)} 条噪音 → 剩余 {len(passed)} 条待分析")

        if not passed:
            console.print("[dim]所有消息均为闲聊，无需 LLM 分析[/dim]")
            return

        try:
            knowledge_items, summary = await svc.analyze_messages(messages)
        except TokenBudgetExceeded as e:
            console.print(f"[red]✗ {e}[/red]")
            return

        if knowledge_items:
            from app.services.knowledge import save_knowledge_items
            async with aiosqlite.connect(str(DB_PATH)) as db:
                await save_knowledge_items(db, knowledge_items)
                await db.commit()

        # Display results
        if knowledge_items:
            table = Table(title="知识点")
            table.add_column("紧急", width=4)
            table.add_column("标签", width=16)
            table.add_column("来源", width=15)
            table.add_column("标题", width=20)
            table.add_column("内容", width=50)

            for item in sorted(knowledge_items, key=lambda m: m.get("urgency", 0), reverse=True):
                urgency = item.get("urgency", 1)
                color = {5: "red", 4: "yellow", 3: "green", 2: "blue", 1: "dim"}.get(urgency, "white")
                table.add_row(
                    f"[{color}]{'★' * urgency}[/{color}]",
                    ", ".join(item.get("tags", []))[:16],
                    item.get("source_chat", "?")[:15],
                    item.get("title", "")[:20],
                    item.get("content", "")[:50],
                )

            console.print(table)
        else:
            console.print("[dim]未发现知识点[/dim]")

        if summary:
            console.print(Panel(summary, title="对话总结"))

        console.print(
            f"\n[green]✓ 分析完成: {len(knowledge_items)} 个知识点 / {len(noise)} 条噪音[/green]"
        )

    asyncio.run(_run())


@cli.command()
@click.option("--keyword", "-k", required=True, help="搜索关键词")
@click.option("--platform", "-p", default=None, help="平台筛选 (wechat/qq/telegram)")
@click.option("--category", "-c", default=None, help="分类筛选 (important/todo/casual)")
@click.option("--since", default=None, help="起始时间")
@click.option("--until", default=None, help="结束时间")
def search(keyword: str, platform: str | None, category: str | None, since: str | None, until: str | None):
    """搜索消息"""
    import aiosqlite
    from app.core.database import DB_PATH

    async def _run():
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row

            conditions = ["m.content LIKE ?"]
            params: list = [f"%{keyword}%"]

            if platform:
                conditions.append("m.platform = ?")
                params.append(platform)
            if category:
                conditions.append("a.category = ?")
                params.append(category)
            if since:
                conditions.append("m.timestamp >= ?")
                params.append(since)
            if until:
                conditions.append("m.timestamp <= ?")
                params.append(until)

            where = " AND ".join(conditions)
            query = f"""
                SELECT m.*, a.category, a.urgency, a.summary
                FROM messages m
                LEFT JOIN analysis_results a ON a.message_id = m.id
                WHERE {where}
                ORDER BY m.timestamp DESC LIMIT 50
            """
            rows = await db.execute_fetchall(query, params)

        if not rows:
            console.print(f"[dim]未找到包含「{keyword}」的消息[/dim]")
            return

        table = Table(title=f"搜索结果: {keyword}")
        table.add_column("平台", width=8)
        table.add_column("来源", width=15)
        table.add_column("内容", width=40)
        table.add_column("分类", width=10)
        table.add_column("紧急", width=4)
        table.add_column("时间", width=19)

        for r in rows:
            r = dict(r)
            table.add_row(
                r.get("platform", ""),
                r.get("chat_name", "")[:15],
                r.get("content", "")[:40],
                r.get("category", "-"),
                str(r.get("urgency", "-")),
                str(r.get("timestamp", ""))[:19],
            )

        console.print(table)

    asyncio.run(_run())


@cli.command()
@click.option("--add-vip", default=None, help="添加 VIP 联系人")
@click.option("--add-chat", default=None, help="添加群聊到筛选列表")
@click.option("--remove-chat", default=None, help="从筛选列表移除群聊")
@click.option("--filter-mode", default=None, type=click.Choice(["whitelist", "blacklist"]), help="筛选模式")
@click.option("--budget", default=None, type=int, help="设置每日 token 预算")
@click.option("--show", is_flag=True, help="显示当前配置")
def config(add_vip: str | None, add_chat: str | None, remove_chat: str | None,
           filter_mode: str | None, budget: int | None, show: bool):
    """查看或修改配置"""
    cfg = load_config()

    if show or not any([add_vip, add_chat, remove_chat, filter_mode, budget]):
        console.print(Panel(cfg.model_dump_json(indent=2), title="当前配置"))
        return

    if add_vip:
        cfg.vip_contacts.append(add_vip)
        console.print(f"[green]✓ 已添加 VIP 联系人: {add_vip}[/green]")
    if add_chat:
        if add_chat not in cfg.chat_filter.chats:
            cfg.chat_filter.chats.append(add_chat)
        console.print(f"[green]✓ 已添加群聊到筛选列表: {add_chat}[/green]")
    if remove_chat:
        if remove_chat in cfg.chat_filter.chats:
            cfg.chat_filter.chats.remove(remove_chat)
        console.print(f"[green]✓ 已从筛选列表移除: {remove_chat}[/green]")
    if filter_mode:
        cfg.chat_filter.mode = filter_mode
        mode_desc = "白名单（只分析列表中的群）" if filter_mode == "whitelist" else "黑名单（分析除列表外的群）"
        console.print(f"[green]✓ 筛选模式: {mode_desc}[/green]")
    if budget:
        cfg.daily_token_budget = budget
        console.print(f"[green]✓ 每日 token 预算: {budget}[/green]")

    save_config(cfg)


def main():
    cli()


if __name__ == "__main__":
    main()
