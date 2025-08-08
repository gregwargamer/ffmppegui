//logique de dispatch des jobs vers les agents
use std::sync::Arc;
use crate::state::AppState;

//tentative de distribution (bouchon)
pub async fn try_dispatch(_state: Arc<AppState>) {
    //TODO: choisir un agent et envoyer un lease via le canal associé
}
