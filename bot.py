import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from datetime import datetime

# ── Configuration ──────────────────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN", "VOTRE_TOKEN_ICI")
DATA_FILE = "data.json"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ── Helpers JSON ───────────────────────────────────────────────────────────────
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"matches": {}, "pronostics": {}, "scores": {}}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def calc_points(prono: dict, result: dict) -> int:
    """
    5 pts : bon vainqueur + score exact
    3 pts : bon vainqueur + bonne différence de buts
    2 pts : match nul correct (score inexact)
    1 pt  : bon vainqueur uniquement
    """
    g1p, g2p = prono["goals1"], prono["goals2"]
    g1r, g2r = result["goals1"], result["goals2"]

    if g1p == g1r and g2p == g2r:
        return 5
    winner_pred = "1" if g1p > g2p else ("2" if g2p > g1p else "N")
    winner_real = "1" if g1r > g2r else ("2" if g2r > g1r else "N")
    if winner_pred != winner_real:
        return 0
    if winner_pred == "N":
        return 2
    if (g1p - g2p) == (g1r - g2r):
        return 3
    return 1

# ── Événements ─────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Bot connecté : {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔄 {len(synced)} commandes slash synchronisées")
    except Exception as e:
        print(f"Erreur sync : {e}")

# ── Commandes Admin ─────────────────────────────────────────────────────────────

@bot.tree.command(name="ajout_match", description="[Admin] Ajoute un match au programme")
@app_commands.describe(
    match_id="Identifiant unique (ex: FRA_BRA)",
    equipe1="Équipe 1",
    equipe2="Équipe 2",
    date="Date du match (ex: 2026-06-15 20:00)"
)
@app_commands.checks.has_permissions(administrator=True)
async def ajout_match(inter: discord.Interaction, match_id: str, equipe1: str, equipe2: str, date: str):
    data = load_data()
    match_id = match_id.upper()
    data["matches"][match_id] = {
        "equipe1": equipe1,
        "equipe2": equipe2,
        "date": date,
        "closed": False,
        "result": None
    }
    save_data(data)
    embed = discord.Embed(
        title="⚽ Nouveau match ajouté",
        color=0x00b300,
        description=f"**{equipe1}** 🆚 **{equipe2}**\n📅 {date}"
    )
    embed.set_footer(text=f"ID : {match_id}")
    await inter.response.send_message(embed=embed)


@bot.tree.command(name="fermer_pronos", description="[Admin] Ferme les pronostics d'un match")
@app_commands.describe(match_id="ID du match à fermer")
@app_commands.checks.has_permissions(administrator=True)
async def fermer_pronos(inter: discord.Interaction, match_id: str):
    data = load_data()
    match_id = match_id.upper()
    if match_id not in data["matches"]:
        await inter.response.send_message("❌ Match introuvable.", ephemeral=True)
        return
    data["matches"][match_id]["closed"] = True
    save_data(data)
    m = data["matches"][match_id]
    await inter.response.send_message(
        f"🔒 Pronostics fermés pour **{m['equipe1']} vs {m['equipe2']}** (`{match_id}`)"
    )


@bot.tree.command(name="resultat", description="[Admin] Entre le résultat officiel d'un match et calcule les points")
@app_commands.describe(
    match_id="ID du match",
    buts1="Buts équipe 1",
    buts2="Buts équipe 2"
)
@app_commands.checks.has_permissions(administrator=True)
async def resultat(inter: discord.Interaction, match_id: str, buts1: int, buts2: int):
    data = load_data()
    match_id = match_id.upper()
    if match_id not in data["matches"]:
        await inter.response.send_message("❌ Match introuvable.", ephemeral=True)
        return

    m = data["matches"][match_id]
    result = {"goals1": buts1, "goals2": buts2}
    m["result"] = result
    m["closed"] = True

    # Calcul des points
    awarded = []
    for user_id, user_pronos in data["pronostics"].items():
        if match_id in user_pronos:
            pts = calc_points(user_pronos[match_id], result)
            if user_id not in data["scores"]:
                data["scores"][user_id] = 0
            data["scores"][user_id] += pts
            if pts > 0:
                awarded.append((user_id, pts))

    save_data(data)

    embed = discord.Embed(
        title=f"🏁 Résultat : {m['equipe1']} {buts1} - {buts2} {m['equipe2']}",
        color=0xf1c40f
    )
    if awarded:
        lines = "\n".join(f"<@{uid}> → **+{pts} pt{'s' if pts>1 else ''}**" for uid, pts in awarded)
        embed.add_field(name="🏆 Points attribués", value=lines, inline=False)
    else:
        embed.add_field(name="Points", value="Aucun pronostic enregistré pour ce match.", inline=False)

    await inter.response.send_message(embed=embed)


# ── Commandes Joueurs ───────────────────────────────────────────────────────────

@bot.tree.command(name="matchs", description="Affiche tous les matchs disponibles pour les pronostics")
async def matchs(inter: discord.Interaction):
    data = load_data()
    if not data["matches"]:
        await inter.response.send_message("Aucun match programmé pour l'instant.", ephemeral=True)
        return

    embed = discord.Embed(title="⚽ Matchs Coupe du Monde 2026", color=0x3498db)
    for mid, m in data["matches"].items():
        status = "🔒 Fermé" if m["closed"] else "✅ Ouvert"
        result_str = ""
        if m.get("result"):
            r = m["result"]
            result_str = f" → {r['goals1']}-{r['goals2']}"
        embed.add_field(
            name=f"`{mid}` — {m['equipe1']} vs {m['equipe2']}",
            value=f"📅 {m['date']} | {status}{result_str}",
            inline=False
        )
    await inter.response.send_message(embed=embed)


@bot.tree.command(name="prono", description="Pose ton pronostic pour un match")
@app_commands.describe(
    match_id="ID du match (voir /matchs)",
    buts1="Buts pour l'équipe 1",
    buts2="Buts pour l'équipe 2"
)
async def prono(inter: discord.Interaction, match_id: str, buts1: int, buts2: int):
    data = load_data()
    match_id = match_id.upper()

    if match_id not in data["matches"]:
        await inter.response.send_message("❌ Match introuvable. Utilise `/matchs` pour voir la liste.", ephemeral=True)
        return

    m = data["matches"][match_id]
    if m["closed"]:
        await inter.response.send_message("🔒 Les pronostics sont fermés pour ce match.", ephemeral=True)
        return

    user_id = str(inter.user.id)
    if user_id not in data["pronostics"]:
        data["pronostics"][user_id] = {}

    data["pronostics"][user_id][match_id] = {"goals1": buts1, "goals2": buts2}
    save_data(data)

    embed = discord.Embed(
        title="✅ Pronostic enregistré !",
        color=0x2ecc71,
        description=f"**{m['equipe1']}** {buts1} - {buts2} **{m['equipe2']}**"
    )
    embed.set_footer(text=f"Match : {match_id} | Tu peux modifier jusqu'à la fermeture")
    await inter.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="mes_pronos", description="Affiche tous tes pronostics")
async def mes_pronos(inter: discord.Interaction):
    data = load_data()
    user_id = str(inter.user.id)
    user_pronos = data["pronostics"].get(user_id, {})

    if not user_pronos:
        await inter.response.send_message("Tu n'as encore aucun pronostic enregistré.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"📋 Pronostics de {inter.user.display_name}",
        color=0x9b59b6
    )
    for mid, p in user_pronos.items():
        m = data["matches"].get(mid, {})
        name = f"{m.get('equipe1','?')} vs {m.get('equipe2','?')}" if m else mid
        result_str = ""
        if m.get("result"):
            r = m["result"]
            pts = calc_points(p, r)
            result_str = f" | Résultat : {r['goals1']}-{r['goals2']} → **{pts} pt{'s' if pts>1 else ''}**"
        embed.add_field(
            name=f"`{mid}` {name}",
            value=f"Ton prono : {p['goals1']}-{p['goals2']}{result_str}",
            inline=False
        )
    total = data["scores"].get(user_id, 0)
    embed.set_footer(text=f"Total : {total} points")
    await inter.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="classement", description="Affiche le classement général des pronostics")
async def classement(inter: discord.Interaction):
    data = load_data()
    scores = data["scores"]

    if not scores:
        await inter.response.send_message("Aucun point attribué pour l'instant.", ephemeral=True)
        return

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    medals = ["🥇", "🥈", "🥉"]

    embed = discord.Embed(
        title="🏆 Classement Pronostics — Coupe du Monde 2026",
        color=0xf39c12,
        timestamp=datetime.utcnow()
    )
    lines = []
    for i, (uid, pts) in enumerate(sorted_scores[:20]):
        medal = medals[i] if i < 3 else f"**{i+1}.**"
        lines.append(f"{medal} <@{uid}> — **{pts} pt{'s' if pts>1 else ''}**")

    embed.description = "\n".join(lines)
    await inter.response.send_message(embed=embed)


@bot.tree.command(name="regles", description="Affiche les règles et le barème des points")
async def regles(inter: discord.Interaction):
    embed = discord.Embed(
        title="📖 Règles du Pronostic — Coupe du Monde 2026",
        color=0xe74c3c
    )
    embed.add_field(name="Comment jouer ?", value=(
        "1. Consulte les matchs avec `/matchs`\n"
        "2. Pose ton pronostic avec `/prono [ID] [buts1] [buts2]`\n"
        "3. Tu peux modifier ton prono avant la fermeture\n"
        "4. Les points sont calculés automatiquement après le match"
    ), inline=False)
    embed.add_field(name="🎯 Barème des points", value=(
        "🟡 **5 pts** — Bon vainqueur + score exact\n"
        "🟠 **3 pts** — Bon vainqueur + bonne différence de buts\n"
        "🔵 **2 pts** — Match nul correct (score inexact)\n"
        "⚪ **1 pt** — Bon vainqueur uniquement\n"
        "❌ **0 pt** — Mauvais vainqueur"
    ), inline=False)
    embed.add_field(name="📋 Commandes disponibles", value=(
        "`/matchs` — Liste des matchs\n"
        "`/prono` — Poser un pronostic\n"
        "`/mes_pronos` — Voir mes pronostics\n"
        "`/classement` — Classement général\n"
        "`/stats` — Statistiques générales\n"
        "`/regles` — Ce message"
    ), inline=False)
    await inter.response.send_message(embed=embed)


@bot.tree.command(name="stats", description="[Admin] Affiche les statistiques générales du concours")
@app_commands.checks.has_permissions(administrator=True)
async def stats(inter: discord.Interaction):
    data = load_data()

    # Nombre de participants (ayant posé au moins 1 prono)
    nb_participants = len(data["pronostics"])

    # Nombre total de pronostics posés
    nb_pronos = sum(len(p) for p in data["pronostics"].values())

    # Nombre de matchs total / terminés / ouverts
    nb_matchs_total = len(data["matches"])
    nb_matchs_termines = sum(1 for m in data["matches"].values() if m.get("result"))
    nb_matchs_ouverts = sum(1 for m in data["matches"].values() if not m["closed"])

    # Meilleur joueur
    meilleur = None
    meilleur_pts = 0
    if data["scores"]:
        meilleur_id, meilleur_pts = max(data["scores"].items(), key=lambda x: x[1])
        meilleur = f"<@{meilleur_id}> avec **{meilleur_pts} pts**"

    # Moyenne de points par participant
    moyenne = 0
    if nb_participants > 0 and data["scores"]:
        moyenne = round(sum(data["scores"].values()) / nb_participants, 1)

    embed = discord.Embed(
        title="📊 Statistiques — Coupe du Monde 2026",
        color=0x1abc9c,
        timestamp=datetime.utcnow()
    )
    embed.add_field(
        name="👥 Participants",
        value=f"**{nb_participants}** joueur{'s' if nb_participants > 1 else ''}",
        inline=True
    )
    embed.add_field(
        name="🎯 Pronostics posés",
        value=f"**{nb_pronos}** au total",
        inline=True
    )
    embed.add_field(
        name="⚽ Matchs",
        value=f"**{nb_matchs_termines}** terminé{'s' if nb_matchs_termines > 1 else ''} / **{nb_matchs_total}** au programme\n**{nb_matchs_ouverts}** ouvert{'s' if nb_matchs_ouverts > 1 else ''} aux pronos",
        inline=False
    )
    if meilleur:
        embed.add_field(
            name="🏆 Meilleur joueur",
            value=meilleur,
            inline=True
        )
    embed.add_field(
        name="📈 Moyenne des points",
        value=f"**{moyenne} pts** par joueur",
        inline=True
    )
    await inter.response.send_message(embed=embed)


@stats.error
async def stats_error(inter: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await inter.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)


# ── Lancement ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
