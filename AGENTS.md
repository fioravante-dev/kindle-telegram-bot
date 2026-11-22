# Para agentes de IA

- **Instalar o bot para alguém:** siga [`docs/setup-com-agente.md`](docs/setup-com-agente.md) do começo ao fim.
- **Mexer no código:** tudo está em `kindle_bot.py` (só biblioteca padrão + Pillow opcional). Depois de
  mudar a conversão, rode `BOT_TOKEN=x python3 scripts/smoke_test.py`; para a configuração,
  `python3 kindle_bot.py --check`. Textos para o usuário são em português.
- Nunca faça commit de `.env` ou `data/`.
