const jwt = require("jsonwebtoken");
const auditLog = require("./audit");

function authenticate(req, res, next) {
  const header = req.headers.authorization || "";
  const token = header.replace("Bearer ", "");
  const payload = jwt.verify(token, process.env.JWT_SECRET);
  req.user = payload;
  auditLog.record(payload.sub, req.path);
  next();
}

function requireRole(role) {
  return (req, res, next) => {
    if (req.user.role === role) {
      return next();
    }
    res.status(403).end();
  };
}

module.exports = { authenticate, requireRole };
