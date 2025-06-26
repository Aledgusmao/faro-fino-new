# Faro Fino - Versão Final 2.0
# Corrige o SyntaxError na montagem da URL de busca.

import os
import json
import logging
import asyncio
import httpx
from datetime import datetime, timedelta
import pytz
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
from bs4 import BeautifulSoup
from email.utils import parsedate_to_datetime
import time
from urllib.parse import quote

# --- CONFIGURAÇÕES ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CONFIG_PATH = "faro_fino_config.json"
MONITORAMENTO_INTERVAL = 300
TIMEZONE_BR = pytz.timezone('America/Sao_Paulo')
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
DEFAULT_CONFIG = {"owner_id": None, "keywords": [], "monitoring_on": False, "history": set()}

# --- FUNÇÕES DE DADOS ---
def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
            config['history'] = set(config.get('history', []))
            return config
    return DEFAULT_CONFIG.copy()

def save_config(config):
    to_save = config.copy()
    to_save['history'] = list(to_save.get('history', set()))
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(to_save, f, indent=4)

# --- LÓGICA DE BUSCA ---
async def fetch_news(keywords: list):
    news_items = []
    if not keywords: return news_items
    
    # --- CORREÇÃO DO SYNTAXERROR ---
    # Monta a query de forma segura, usando aspas simples e duplas corretamente.
    query_parts = [f'"{k}"' for k in keywords]
    query = " OR ".join(query_parts)
    # Codifica a query para ser usada em uma URL
    encoded_query = quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=pt-BR&gl=BR&ceid=BR:pt-419&tbs=qdr:h"
    # --- FIM DA CORREÇÃO ---

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=20)
        soup = BeautifulSoup(response.content, 'lxml-xml')
        for item in soup.find_all('item'):
            pub_date = parsedate_to_datetime(item.find('pubDate').text).astimezone(TIMEZONE_BR)
            news_items.append({'title': item.find('title').text, 'link': item.find('link').text, 'source': item.find('source').text, 'date': pub_date})
    except Exception as e:
        logger.error(f"Erro na busca: {e}")
    return news_items

async def process_news(bot: Bot):
    config = load_config()
    owner_id, keywords = config.get("owner_id"), config.get("keywords")
    if not owner_id or not keywords or not config.get("monitoring_on"):
        if config.get("monitoring_on"):
            await bot.send_message(chat_id=owner_id, text="_[Auto] Verificação pulada. Adicione palavras-chave._", parse_mode=ParseMode.MARKDOWN)
        return
    
    found_news = await fetch_news(keywords)
    new_articles = []
    date_limit = datetime.now(TIMEZONE_BR) - timedelta(days=3)

    for article in found_news:
        if article['link'] in config['history'] or (article['date'] and article['date'] < date_limit): continue
        text_to_check = f"{article['title']} {article['source']}".lower()
        if any(k.lower() in text_to_check for k in keywords):
            new_articles.append(article)
            config['history'].add(article['link'])
            
    if new_articles:
        for article in new_articles:
            date_str = article['date'].strftime('%d/%m/%Y %H:%M')
            message = (f"📰 *{article['title']}*\n\n"
                       f"📅 *Publicado em:* {date_str}\n"
                       f"🌐 *Fonte:* {article['source']}\n"
                       f"🔗 [Clique para ler]({article['link']})")
            await bot.send_message(chat_id=owner_id, text=message, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            await asyncio.sleep(1)

    save_config(config)
    ping_msg = f"_[Auto] Verificação concluída. {len(new_articles)} novas notícias._"
    await bot.send_message(chat_id=owner_id, text=ping_msg, parse_mode=ParseMode.MARKDOWN)

# --- LOOP PRINCIPAL E HANDLERS ---
async def handle_updates(bot: Bot, update: Update):
    # Esta função agora é um dispatcher simples
    # O contexto é criado aqui para garantir que os handlers tenham o que precisam
    context = ContextTypes.DEFAULT_TYPE(application=Application.builder().token(BOT_TOKEN).build(), chat_id=update.effective_chat.id, user_id=update.effective_user.id)
    if update.message and update.message.text:
        text = update.message.text
        if text.startswith('/start'): await start(update, context)
        elif text.startswith('/monitoramento'): await toggle_monitoring(update, context)
        elif text.startswith('/verificar'): await check_now(update, context)
        elif text.startswith(('@', '#')): await text_handler(update, context)
        # Adicione outros comandos aqui...

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    if config.get('owner_id') is None:
        config['owner_id'] = update.effective_user.id
        save_config(config)
        await update.message.reply_text("Bem-vindo! Você foi definido como proprietário.")
    else:
        await update.message.reply_text("Bem-vindo de volta!")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    if update.effective_user.id != config.get('owner_id'): return
    
    text = update.message.text.strip()
    keywords = set(config.get('keywords', []))
    
    if text.startswith('@'):
        added = {k.strip() for k in text[1:].split(',') if k.strip()}
        keywords.update(added)
        msg = f"✅ Adicionados: {len(added)} itens."
    elif text.startswith('#'):
        removed = {k.strip() for k in text[1:].split(',') if k.strip()}
        keywords.difference_update(removed)
        msg = f"🗑️ Removidos: {len(removed)} itens."
    else: return
        
    config['keywords'] = sorted(list(keywords))
    save_config(config)
    await update.message.reply_text(msg)

async def toggle_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    config['monitoring_on'] = not config.get('monitoring_on', False)
    save_config(config)
    await update.message.reply_text(f"Monitoramento {'ATIVADO' if config['monitoring_on'] else 'DESATIVADO'}.")

async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Iniciando verificação manual...")
    await process_news(context.bot)
    await update.message.reply_text("Verificação manual concluída.")

async def main():
    bot = Bot(BOT_TOKEN)
    update_id = 0
    
    logger.info("🚀 Faro Fino - Edição Simplificada iniciada!")

    # Limpa atualizações pendentes
    updates = await bot.get_updates(offset=-1, timeout=1)
    if updates:
        update_id = updates[-1].update_id + 1

    while True:
        try:
            updates = await bot.get_updates(offset=update_id, timeout=10)
            for update in updates:
                update_id = update.update_id + 1
                await handle_updates(bot, update)
            
            # Executa a verificação de notícias no mesmo loop
            await process_news(bot)
            
            # Pausa antes do próximo ciclo completo
            await asyncio.sleep(MONITORAMENTO_INTERVAL)
        except Exception as e:
            logger.error(f"Erro no loop principal: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    if not BOT_TOKEN:
        logger.error("ERRO: BOT_TOKEN não configurado!"); exit()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot encerrado pelo usuário.")
