import discord
from discord.ext import commands
from discord import app_commands
import os
import psycopg2
import psycopg2.extras
import json
from datetime import datetime

# ── Configuration ──────────────────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN", "VOTRE_TOKEN_ICI")
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ── Base de données ─────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS matches (
                    match_id TEXT PRIMARY KEY,
                    equipe1 TEXT NOT NULL,
                    equipe2 TEXT NOT NULL,
                    date TEXT NOT NULL,
                    closed BOOLEAN DEFAULT FALSE,
                    goals1 INTEGER,
                    goals2 INTEGER
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pronostics (
                    user_id TEXT NOT NULL,
                    match_id TEXT NOT NULL,
                    goals1 INTEGER NOT NULL,
                    goals2 INTEGER NOT NULL,
                    PRIMARY KEY (user_id, match_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS scores (
                    user_id TEXT PRIMARY KEY,
                    total INTEGER DEFAULT 0
                )
            """)
        conn.commit()

def calc_points(g1p, g2p, g1r, g2r) -> int:
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
    init_db()
    print(f"✅ Bot connecté : {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔄 {len(synced)} commandes slash synchronisées")
    except Exception as e:
        print(f"Erreur sync : {e}")

# ── Commandes Admin ─────────────────────────────────────────────────────────────

@bot.tree.command(name="ajout_match", description="[Admin] Ajoute un match au programme")
@app_commands.describe(
    match_id="Identifiant unique (ex: FRA_SEN)",
    equipe1="Équipe 1",
    equipe2="Équipe 2",
    date="Date du match (ex: 2026-06-16 20:00)"
)
@app_commands.checks.has_permissions(administrator=True)
async def ajout_match(inter: discord.Interaction, match_id: str, equipe1: str, equipe2: str, date: str):
    match_id = match_id.upper()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO matches (match_id, equipe1, equipe2, date, closed)
                VALUES (%s, %s, %s, %s, FALSE)
                ON CONFLICT (match_id) DO UPDATE SET equipe1=EXCLUDED.equipe1, equipe2=EXCLUDED.equipe2, date=EXCLUDED.date
            """, (match_id, equipe1, equipe2, date))
        conn.commit()
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
    match_id = match_id.upper()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE matches SET closed=TRUE WHERE match_id=%s RETURNING equipe1, equipe2", (match_id,))
            row = cur.fetchone()
        conn.commit()
    if not row:
        await inter.response.send_message("❌ Match introuvable.", ephemeral=True)
        return
    await inter.response.send_message(f"🔒 Pronostics fermés pour **{row[0]} vs {row[1]}** (`{match_id}`)")


@bot.tree.command(name="resultat", description="[Admin] Entre le résultat officiel et calcule les points")
@app_commands.describe(match_id="ID du match", buts1="Buts équipe 1", buts2="Buts équipe 2")
@app_commands.checks.has_permissions(administrator=True)
async def resultat(inter: discord.Interaction, match_id: str, buts1: int, buts2: int):
    match_id = match_id.upper()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT equipe1, equipe2 FROM matches WHERE match_id=%s", (match_id,))
            row = cur.fetchone()
            if not row:
                await inter.response.send_message("❌ Match introuvable.", ephemeral=True)
                return
            equipe1, equipe2 = row
            cur.execute("UPDATE matches SET goals1=%s, goals2=%s, closed=TRUE WHERE match_id=%s", (buts1, buts2, match_id))
            cur.execute("SELECT user_id, goals1, goals2 FROM pronostics WHERE match_id=%s", (match_id,))
            pronos = cur.fetchall()
            awarded = []
            for user_id, g1p, g2p in pronos:
                pts = calc_points(g1p, g2p, buts1, buts2)
                if pts > 0:
                    cur.execute("""
                        INSERT INTO scores (user_id, total) VALUES (%s, %s)
                        ON CONFLICT (user_id) DO UPDATE SET total = scores.total + EXCLUDED.total
                    """, (user_id, pts))
                    awarded.append((user_id, pts))
        conn.commit()

    embed = discord.Embed(
        title=f"🏁 Résultat : {equipe1} {buts1} - {buts2} {equipe2}",
        color=0xf1c40f
    )
    if awarded:
        lines = "\n".join(f"<@{uid}> → **+{pts} pt{'s' if pts>1 else ''}**" for uid, pts in awarded)
        embed.add_field(name="🏆 Points attribués", value=lines, inline=False)
    else:
        embed.add_field(name="Points", value="Aucun pronostic enregistré pour ce match.", inline=False)
    await inter.response.send_message(embed=embed)


# ── Commandes Joueurs ───────────────────────────────────────────────────────────

@bot.tree.command(name="matchs", description="Affiche tous les matchs disponibles")
async def matchs(inter: discord.Interaction):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT match_id, equipe1, equipe2, date, closed, goals1, goals2 FROM matches ORDER BY date")
            rows = cur.fetchall()
    if not rows:
        await inter.response.send_message("Aucun match programmé pour l'instant.", ephemeral=True)
        return
    embed = discord.Embed(title="⚽ Matchs Coupe du Monde 2026", color=0x3498db)
    for mid, e1, e2, date, closed, g1, g2 in rows:
        status = "🔒 Fermé" if closed else "✅ Ouvert"
        result_str = f" → {g1}-{g2}" if g1 is not None else ""
        embed.add_field(
            name=f"`{mid}` — {e1} vs {e2}",
            value=f"📅 {date} | {status}{result_str}",
            inline=False
        )
    await inter.response.send_message(embed=embed)


@bot.tree.command(name="prono", description="Pose ton pronostic pour un match")
@app_commands.describe(match_id="ID du match (voir /matchs)", buts1="Buts équipe 1", buts2="Buts équipe 2")
async def prono(inter: discord.Interaction, match_id: str, buts1: int, buts2: int):
    match_id = match_id.upper()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT equipe1, equipe2, closed FROM matches WHERE match_id=%s", (match_id,))
            row = cur.fetchone()
            if not row:
                await inter.response.send_message("❌ Match introuvable. Utilise `/matchs` pour voir la liste.", ephemeral=True)
                return
            e1, e2, closed = row
            if closed:
                await inter.response.send_message("🔒 Les pronostics sont fermés pour ce match.", ephemeral=True)
                return
            user_id = str(inter.user.id)
            cur.execute("""
                INSERT INTO pronostics (user_id, match_id, goals1, goals2) VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, match_id) DO UPDATE SET goals1=EXCLUDED.goals1, goals2=EXCLUDED.goals2
            """, (user_id, match_id, buts1, buts2))
        conn.commit()
    embed = discord.Embed(
        title="✅ Pronostic enregistré !",
        color=0x2ecc71,
        description=f"**{e1}** {buts1} - {buts2} **{e2}**"
    )
    embed.set_footer(text=f"Match : {match_id} | Tu peux modifier jusqu'à la fermeture")
    await inter.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="mes_pronos", description="Affiche tous tes pronostics")
async def mes_pronos(inter: discord.Interaction):
    user_id = str(inter.user.id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.match_id, m.equipe1, m.equipe2, p.goals1, p.goals2, m.goals1, m.goals2
                FROM pronostics p JOIN matches m ON p.match_id = m.match_id
                WHERE p.user_id = %s
            """, (user_id,))
            rows = cur.fetchall()
            cur.execute("SELECT total FROM scores WHERE user_id=%s", (user_id,))
            score_row = cur.fetchone()
    if not rows:
        await inter.response.send_message("Tu n'as encore aucun pronostic enregistré.", ephemeral=True)
        return
    embed = discord.Embed(title=f"📋 Pronostics de {inter.user.display_name}", color=0x9b59b6)
    for mid, e1, e2, g1p, g2p, g1r, g2r in rows:
        result_str = ""
        if g1r is not None:
            pts = calc_points(g1p, g2p, g1r, g2r)
            result_str = f" | Résultat : {g1r}-{g2r} → **{pts} pt{'s' if pts>1 else ''}**"
        embed.add_field(
            name=f"`{mid}` {e1} vs {e2}",
            value=f"Ton prono : {g1p}-{g2p}{result_str}",
            inline=False
        )
    total = score_row[0] if score_row else 0
    embed.set_footer(text=f"Total : {total} points")
    await inter.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="classement", description="Affiche le classement général")
async def classement(inter: discord.Interaction):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, total FROM scores ORDER BY total DESC LIMIT 20")
            rows = cur.fetchall()
    if not rows:
        await inter.response.send_message("Aucun point attribué pour l'instant.", ephemeral=True)
        return
    medals = ["🥇", "🥈", "🥉"]
    embed = discord.Embed(
        title="🏆 Classement Pronostics — Coupe du Monde 2026",
        color=0xf39c12,
        timestamp=datetime.utcnow()
    )
    lines = []
    for i, (uid, pts) in enumerate(rows):
        medal = medals[i] if i < 3 else f"**{i+1}.**"
        lines.append(f"{medal} <@{uid}> — **{pts} pt{'s' if pts>1 else ''}**")
    embed.description = "\n".join(lines)
    await inter.response.send_message(embed=embed)


@bot.tree.command(name="regles", description="Affiche les règles et le barème des points")
async def regles(inter: discord.Interaction):
    embed = discord.Embed(title="📖 Règles du Pronostic — Coupe du Monde 2026", color=0xe74c3c)
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
        "`/stats` — Statistiques (admin)\n"
        "`/regles` — Ce message"
    ), inline=False)
    await inter.response.send_message(embed=embed)


@bot.tree.command(name="stats", description="[Admin] Affiche les statistiques générales du concours")
@app_commands.checks.has_permissions(administrator=True)
async def stats(inter: discord.Interaction):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT user_id) FROM pronostics")
            nb_participants = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM pronostics")
            nb_pronos = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM matches")
            nb_matchs_total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM matches WHERE goals1 IS NOT NULL")
            nb_matchs_termines = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM matches WHERE closed=FALSE")
            nb_matchs_ouverts = cur.fetchone()[0]
            cur.execute("SELECT user_id, total FROM scores ORDER BY total DESC LIMIT 1")
            best = cur.fetchone()
            cur.execute("SELECT AVG(total) FROM scores")
            moyenne = cur.fetchone()[0]

    embed = discord.Embed(
        title="📊 Statistiques — Coupe du Monde 2026",
        color=0x1abc9c,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="👥 Participants", value=f"**{nb_participants}** joueur{'s' if nb_participants > 1 else ''}", inline=True)
    embed.add_field(name="🎯 Pronostics posés", value=f"**{nb_pronos}** au total", inline=True)
    embed.add_field(
        name="⚽ Matchs",
        value=f"**{nb_matchs_termines}** terminé{'s' if nb_matchs_termines > 1 else ''} / **{nb_matchs_total}** au programme\n**{nb_matchs_ouverts}** ouvert{'s' if nb_matchs_ouverts > 1 else ''} aux pronos",
        inline=False
    )
    if best:
        embed.add_field(name="🏆 Meilleur joueur", value=f"<@{best[0]}> avec **{best[1]} pts**", inline=True)
    if moyenne:
        embed.add_field(name="📈 Moyenne des points", value=f"**{round(moyenne, 1)} pts** par joueur", inline=True)
    await inter.response.send_message(embed=embed, ephemeral=True)


@stats.error
async def stats_error(inter: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await inter.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)


# ── Lancement ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
