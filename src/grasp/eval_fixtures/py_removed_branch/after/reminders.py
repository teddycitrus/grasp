def send_reminders(users, mailer, today):
    sent = 0
    for user in users:
        if not user.email or user.unsubscribed:
            continue
        mailer.send(user.email, "Your weekly summary")
        user.last_reminder = today
        sent += 1
    mailer.flush()
    return sent
