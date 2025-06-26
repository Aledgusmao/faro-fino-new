# Faro Fino - Versão Final para Reinstalação
# Arquitetura estável e completa.

import os, json, logging, asyncio, httpx, time, re
from datetime import datetime, timedelta
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
from bs4 import BeautifulSoup
from email.utils import parsedate_to_datetime

# --- CONFIGURAÇÕES ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CONFIG_PATH = "faro_fino_config.json"
MONITORAMENTO_INTERVAL = 300
TIMEZONE_BR = pytz.timezone('America/Sao_Paulo')
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
DEFAULT_CONFIG = {"owner_id": None, "keywords": [], "monitoring_on": False, "history": set()}

# --- DADOS ---
def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f); config['history'] = set(config.get('history', [])); return config
    return DEFAULT_CONFIG.copy()

def save_config(config):
    to_save = config.copy(); to_save['history'] = list(to_save.get('history', set()))
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f: json.dump(to_save, f, indent=4)
    logger.info("Configuração salva.")

# --- LÓGICA DE BUSCA ---
async def fetch_news(keywords: list):
    news = []
    if not keywords: return news
    url = f"https://news.google.com/rss/search?q={' OR '.join(f'\"{k}\"' for k in keywords)}&hl=pt-BR&gl=BR&ceid=BR:pt-419&tbs=qdr:h"
    try:
        async with httpx.AsyncClient() as c: response = await c.get(url, timeout=20)
        soup = BeautifulSoup(response.content, 'lxml-xml')
        for item in soup.find_all('item'):
            news.append({'title': item.title.text, 'link': item.link.text, 'source': item.source.text, 'date': parsedate_to_datetime(item.pubDate.text).astimezone(TIMEZONE_BR)})
    except Exception as e: logger.error(f"Erro na busca: {e}")
    return news

async def process_news(context: ContextTypes.DEFAULT_TYPE, is_manual=False):
    config = load_config()
    owner_id, keywords = config.get("owner_id"), config.get("keywords")
    if not owner_id or not keywords or (not config.get("monitoring_on") and not is_manual): return
    
    found_news = await fetch_news(keywords)
    new_articles = []
    limit = datetime.now(TIMEZONE_BR) - timedelta(days=3)
    for article in found_news:
        if article['link'] in config['history'] or (article['date'] and article['date'] < limit): continue
        text = f"{article['title']} {article['source']}".lower()
        if any(k.lower() in text for k in keywords): new_articles.append(article); config['history'].add(article['link'])
    
    if new_articles:
        for article in new_articles:
            msg = (f"📰 *{article['title']}*\n\n📅 *Em:* {article['date'].strftime('%d/%m %H:%M')}\n"
                   f"🌐 *Fonte:* {article['source']}\n🔗 [Ler]({article['link']})")
            await context.bot.send_message(chat_id=owner_id, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            await asyncio.sleep(1)
    
    save_config(config)
    if not is_manual: await context.bot.send_message(chat_id=owner_id, text=f"_[Auto] Verificação: {len(new_articles)} novas._", parse_mode=ParseMode.MARKDOWN)

# --- COMANDOS ---
def is_owner(update: Update, config: dict): return update.effective_user.id == config.get("owner_id")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    if not config.get('owner_id'):
        config['owner_id'] = update.effective_user.id; save_config(config)
        await update.message.reply_text("Bem-vindo! Para restaurar, encaminhe um backup.")
    else: await update.message.reply_text("Bem-vindo de volta!")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config(); text = update.message.text.strip()
    if not is_owner(update, config) or not text.startswith(('@', '#')): return
    keywords, items = set(config.get('keywords', [])), [k.strip() for k in text[1:].split(',') if k.strip()]
    changed, msg_part = (keywords.update, "Adicionados") if text.startswith('@') else (keywords.difference_update, "Removidos")
    original_len = len(keywords); changed(items); new_len = len(keywords)
    config['keywords'] = sorted(list(keywords)); save_config(config)
    await update.message.reply_text(f"{msg_part}: {new_len - original_len if text.startswith('@') else original_len - new_len} itens.")

async def restore_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document or not "backup" in update.message.document.file_name: return
    file = await update.message.document.get_file(); content = await file.download_as_bytearray()
    backup_data = json.loads(content); config = load_config()
    config.update(backup_data); save_config(config)
    await update.message.reply_text("✅ Backup restaurado!")

async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Iniciando verificação..."); await process_news(context, is_manual=True); await update.message.reply_text("Concluída.")

# --- FUNÇÃO PRINCIPAL ---
async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.Document.FileExtension("json"), restore_handler))
    app.add_handler(CommandHandler('verificar', check_now))
    # Adicione outros comandos simples aqui
    
    # Loop principal simplificado
    update_id = 0
    while True:
        try:
            updates = await app.bot.get_updates(offset=update_id, timeout=10)
            for update in updates:
                update_id = update.update_id + 1
                await app.process_update(update)
            
            # Verificação de notícias integrada ao loop
            await process_news(ContextTypes.DEFAULT_TYPE(application=app))
            await asyncio.sleep(MONITORAMENTO_INTERVAL)
        except Exception as e:
            logger.error(f"Erro fatal no loop principal: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    if not BOT_TOKEN: logger.error("ERRO: BOT_TOKEN não configurado!"); exit()
    asyncio.run(main())