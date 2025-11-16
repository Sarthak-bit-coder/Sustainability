import sqlite3

conn = sqlite3.connect("database.db")
c = conn.cursor()

# Delete all users
c.execute("DELETE FROM users")
conn.commit()

# Optional: reset the auto-increment counter
c.execute("DELETE FROM sqlite_sequence WHERE name='users'")
conn.commit()

conn.close()
print("All users deleted successfully!")