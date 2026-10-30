package store

import (
	"database/sql"
	"errors"
	"fmt"
)

type User struct {
	ID   int
	Name string
}

func GetUser(db *sql.DB, id int) (*User, error) {
	if id <= 0 {
		return nil, fmt.Errorf("invalid user id %d", id)
	}
	row := db.QueryRow("SELECT id, name FROM users WHERE id = ?", id)
	u := &User{}
	if err := row.Scan(&u.ID, &u.Name); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, nil
		}
		return nil, fmt.Errorf("scan user %d: %w", id, err)
	}
	return u, nil
}
