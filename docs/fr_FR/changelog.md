# Changelog

## 0.2

- Bannière d'accueil à l'ouverture du terminal : système, version de Jeedom,
  durée de fonctionnement, charge, mémoire, disque, adresse IP, nombre de
  plugins, température du processeur quand elle est connue.
- Invite en couleur avec le code de sortie de la dernière commande, et titre de
  l'onglet.
- Raccourcis Jeedom : `jlog`, `jcd` (avec complétion), `jerr`, `jplugins`,
  `jinfo`, `jrestart`, `jauteur`, `jhelp` ; historique plus long.
- Nouveau réglage « Bannière d'accueil et raccourcis Jeedom », actif par défaut ;
  décoché, le shell est lancé comme avant.

## 0.1

Première version.

- Terminal interactif dans l'interface de Jeedom, fondé sur xterm.js : couleurs,
  redimensionnement, programmes plein écran, copier-coller.
- Démon Python sans aucune dépendance externe, qui n'écoute que sur la boucle
  locale ; Apache relaie le WebSocket.
- Ouverture réservée aux administrateurs, par un jeton à usage unique valable
  30 secondes.
- Shell sous www-data, root ou un autre compte ; session tmux persistante en
  option.
- Fermeture après inactivité, nombre de sessions limité, restriction au réseau
  local en option.
- L'installation des dépendances configure Apache, vérifie la configuration
  avant de le recharger, et revient en arrière en cas d'erreur. Le relais
  fonctionne aussi quand Jeedom est servi dans un sous-dossier ; il est retiré à
  la désactivation du plugin.
- Démon lancé dans sa propre unité systemd : un redémarrage d'Apache ne ferme
  pas les terminaux.
- Réglages validés à l'enregistrement ; le démon n'est redémarré que si un
  réglage qui le concerne a changé.
- Diagnostic d'une connexion qui échoue, et tests dans la page Santé.
