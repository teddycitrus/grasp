package store

import (
	"database/sql"
)

type User struct {
	ID   int
	Name string
}

func GetUser(db *sql.DB, id int) (*User, error) {
	row := db.QueryRow("SELECT id, name FROM users WHERE id = ?", id)
	u := &User{}
	row.Scan(&u.ID, &u.Name)
	return u, nil
}
