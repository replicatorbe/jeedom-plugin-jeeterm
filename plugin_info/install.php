<?php
/* This file is part of Jeedom.
 *
 * Jeedom is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * Jeedom is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with Jeedom. If not, see <http://www.gnu.org/licenses/>.
 */


require_once __DIR__ . '/../../../core/php/core.inc.php';

/*
 * Le démon n'échange qu'avec PHP, par la boucle locale : l'API du plugin n'a
 * aucune raison d'être joignable depuis l'extérieur. La clé est créée ici en
 * mode « localhost » : créée plus tard par jeedom::getApiKey() sans mode, elle
 * le serait en mode « enable ». Le mode est aussi réimposé, pour corriger une
 * installation où la clé a déjà été créée ainsi.
 */
function jeeterm_apiLocalhost() {
    jeedom::getApiKey('jeeterm', 'localhost');
    config::save('api::jeeterm::mode', 'localhost', 'core');
}

function jeeterm_install() {
    try {
        jeeterm_apiLocalhost();
    } catch (Throwable $e) {
        log::add('jeeterm', 'error', __('Installation du plugin :', __FILE__) . ' ' . $e->getMessage());
    }
}

/*
 * Appelée à chaque mise à jour, par un processus PHP que la requête attend :
 * rien de lent ni de réseau ici. La conf Apache n'est pas réécrite : c'est le
 * travail du script de dépendances, qui tourne en root. Si sa forme a changé
 * (repère APACHE_CONF_MARKER), les dépendances deviennent incomplètes et le
 * coeur relance leur installation.
 */
function jeeterm_update() {
    try {
        jeeterm_apiLocalhost();
        /* Le programme du démon ou la conf attendue ont pu changer : l'état « ok »
         * mis en cache par le coeur pour les dépendances est oublié. */
        cache::byKey('dependancyjeeterm')->remove();
    } catch (Throwable $e) {
        log::add('jeeterm', 'error', __('Mise à jour du plugin :', __FILE__) . ' ' . $e->getMessage());
    }
}

/*
 * Appelée à la désactivation comme à la désinstallation : le coeur ne
 * distingue pas les deux. On arrête le démon et on retire la conf Apache ;
 * à la réactivation, le coeur recalcule les dépendances et les réinstalle si
 * leur gestion automatique est active (sinon, bouton « Relancer »).
 */
function jeeterm_remove() {
    try {
        jeeterm::deamon_stop();
    } catch (Throwable $e) {
        log::add('jeeterm', 'error', __('Arrêt du démon :', __FILE__) . ' ' . $e->getMessage());
    }
    try {
        jeeterm::removeApacheConf();
    } catch (Throwable $e) {
        log::add('jeeterm', 'error', __('Retrait de la configuration Apache :', __FILE__) . ' ' . $e->getMessage());
    }
    message::removeAll('jeeterm', 'settingsChanged');
    message::removeAll('jeeterm', 'unableStartDeamon');
}
