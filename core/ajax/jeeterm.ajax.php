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

try {
    require_once __DIR__ . '/../../../../core/php/core.inc.php';
    include_file('core', 'authentification', 'php');

    /* Un terminal équivaut à un accès root à la machine (www-data a sudo sans
     * mot de passe sur Jeedom) : administrateurs seulement. */
    if (!isConnect('admin')) {
        throw new Exception(__('401 - Accès non autorisé', __FILE__));
    }

    /*
     * ajax::init() sans liste d'actions autorisées en GET : toute action passée
     * en GET est refusée, ce qui interdit de déclencher une session par un
     * simple lien ou une balise image.
     *
     * Contre les requêtes forgées depuis un autre site : le cookie de session
     * de Jeedom est SameSite=Strict (core/php/authentification.php), et Jeedom
     * 4.4+ n'a plus de jeton CSRF (ajax::getToken() est vide). On exige en plus
     * l'en-tête X-Requested-With, qu'un formulaire ne sait pas poser et qu'une
     * requête d'un autre site ne peut poser sans une autorisation CORS que
     * Jeedom ne donne pas.
     */
    ajax::init();
    if (!isset($_SERVER['HTTP_X_REQUESTED_WITH']) || $_SERVER['HTTP_X_REQUESTED_WITH'] !== 'XMLHttpRequest') {
        throw new Exception(__('Requête refusée : en-tête X-Requested-With absent.', __FILE__));
    }

    /* Plugin désactivé : sa classe n'est plus chargée par le coeur, et l'appel
     * finirait sur « Class jeeterm not found ». */
    if (!plugin::byId('jeeterm')->isActive()) {
        throw new Exception(__('Le plugin JeeTerm est désactivé.', __FILE__));
    }

    if (init('action') == 'createSession') {
        unautorizedInDemo();
        $login = $_SESSION['user']->getLogin();
        /* Adresse déclarée (en-têtes de proxy, falsifiables) et adresse réelle
         * de la connexion : les deux sont journalisées quand elles diffèrent. */
        $ip = getClientIp();
        $peer = isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : '';
        if ($peer !== '' && $peer !== $ip) {
            $ip .= ' (' . __('connexion depuis', __FILE__) . ' ' . $peer . ')';
        }
        /* Niveau warning : une ouverture de terminal root se trace même quand
         * le journal du plugin n'est pas en mode info. */
        log::add('jeeterm', 'warning', __('Demande de session par', __FILE__) . ' ' . $login
               . ' ' . __('depuis', __FILE__) . ' ' . $ip);
        try {
            $session = jeeterm::createSession($login, $ip);
        } catch (Throwable $e) {
            log::add('jeeterm', 'warning', __('Session refusée à', __FILE__) . ' ' . $login
                   . ' (' . $ip . ') : ' . $e->getMessage());
            throw $e;
        }
        ajax::success($session);
    }

    if (init('action') == 'diagnose') {
        ajax::success(jeeterm::diagnose());
    }

    throw new Exception(__('Aucune méthode correspondante à :', __FILE__) . ' ' . init('action'));

} catch (Throwable $e) {
    /* Throwable et non Exception : en PHP 8 une Error (méthode inexistante,
     * erreur de type) n'hérite pas d'Exception et donnerait un HTTP 500 muet. */
    ajax::error(displayException($e), $e->getCode());
}
