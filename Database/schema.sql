-- TABLE DES UTILISATEURS (Comptes praticiens)
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(30) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_users_username UNIQUE (username),
    CONSTRAINT chk_username_length CHECK (char_length(username) BETWEEN 3 AND 30)
);

-- TABLE DES PRÉDICTIONS (Historique médical)
CREATE TABLE IF NOT EXISTS predictions (
    id BIGSERIAL PRIMARY KEY,

    -- Clé étrangère vers le praticien.
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

    -- Variables cellulaires (bornes médicales contrôlées en SGBD)
    texture_worst DOUBLE PRECISION NOT NULL
        CONSTRAINT chk_texture_worst CHECK (texture_worst > 10.0 AND texture_worst <= 50.0),
    area_worst DOUBLE PRECISION NOT NULL
        CONSTRAINT chk_area_worst CHECK (area_worst > 150.0 AND area_worst <= 4300.0),
    smoothness_worst DOUBLE PRECISION NOT NULL
        CONSTRAINT chk_smoothness_worst CHECK (smoothness_worst > 0.05 AND smoothness_worst <= 0.25),
    compactness_worst DOUBLE PRECISION NOT NULL
        CONSTRAINT chk_compactness_worst CHECK (compactness_worst >= 0.02 AND compactness_worst <= 1.10),
    concavity_worst DOUBLE PRECISION NOT NULL
        CONSTRAINT chk_concavity_worst CHECK (concavity_worst >= 0.01 AND concavity_worst <= 1.30),
    concave_points_worst DOUBLE PRECISION NOT NULL
        CONSTRAINT chk_concave_points_worst CHECK (concave_points_worst >= 0.02 AND concave_points_worst <= 0.40),
    symmetry_worst DOUBLE PRECISION NOT NULL
        CONSTRAINT chk_symmetry_worst CHECK (symmetry_worst > 0.10 AND symmetry_worst <= 0.70),
    fractal_dimension_worst DOUBLE PRECISION NOT NULL
        CONSTRAINT chk_fractal_dimension_worst CHECK (fractal_dimension_worst > 0.04 AND fractal_dimension_worst <= 0.22),

    -- Résultat du modèle ('M' = Maligne, 'B' = Bénigne)
    prediction CHAR(1) NOT NULL
        CONSTRAINT chk_prediction_format CHECK (prediction IN ('M', 'B')),

    probability_pct DOUBLE PRECISION
        CONSTRAINT chk_probability_range CHECK (probability_pct IS NULL OR probability_pct BETWEEN 0.0 AND 100.0),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- INDEX DE PRODUCTION OPTIMISÉS
    -- Index composite couvrant la requête Streamlit ("Historique du praticien par date")
CREATE INDEX IF NOT EXISTS idx_predictions_user_date ON predictions (user_id, created_at DESC);

    -- Index pour les statistiques et le suivi global de l'API
CREATE INDEX IF NOT EXISTS idx_predictions_label ON predictions (prediction);