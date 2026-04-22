const TelegramBot = require('node-telegram-bot-api');

const token = process.env.BOT_TOKEN;

if (!token) {
  console.log("Token missing");
}

const bot = new TelegramBot(token, { polling: true });

bot.onText(/\/start/, (msg) => {
  bot.sendMessage(msg.chat.id, "✅ Bot is working on Render!");
});

bot.on('message', (msg) => {
  if (msg.text !== '/start') {
    bot.sendMessage(msg.chat.id, "You said: " + msg.text);
  }
});