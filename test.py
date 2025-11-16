import sqlite3

conn = sqlite3.connect("database.db")
c = conn.cursor()

# Add missing columns if needed
c.execute("ALTER TABLE items ADD COLUMN giver TEXT")
c.execute("ALTER TABLE items ADD COLUMN claimer TEXT")

conn.commit()
conn.close()