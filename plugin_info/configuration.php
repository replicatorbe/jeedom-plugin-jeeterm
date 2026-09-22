<?php
if (!isConnect('admin')) {
	throw new Exception('{{401 - Accès non autorisé}}');
}
?>
<form class="form-horizontal">
	<div class="alert alert-danger">
		<i class="fas fa-exclamation-triangle"></i>
		<strong>{{Ce terminal donne un accès root à la machine.}}</strong>
		{{Sur une installation Jeedom, le compte www-data peut utiliser sudo sans mot de passe : même ouvert sous www-data, le shell permet de tout faire, y compris de rendre Jeedom ou le système inutilisable. Seuls les administrateurs de Jeedom peuvent ouvrir le terminal ; ne donnez ce profil qu'à des personnes de confiance.}}
	</div>
	<div class="alert alert-info">
		<i class="fas fa-info-circle"></i>
		{{Les réglages du démon sont appliqués à son redémarrage : si l'un d'eux change, l'enregistrement relance le démon en arrière-plan, ce qui ferme tous les terminaux ouverts (une session tmux, elle, survit). Un changement de port impose en plus de relancer l'installation des dépendances, qui réécrit la configuration Apache.}}
	</div>

	<fieldset>
		<legend><i class="fas fa-terminal"></i> {{Shell}}</legend>
		<div class="form-group">
			<label class="col-lg-4 control-label">{{Compte du shell}}</label>
			<div class="col-lg-2">
				<select class="form-control" id="sel_jeetermShellUser">
					<option value="">www-data</option>
					<option value="root">root</option>
					<option value="other">{{Autre compte}}</option>
				</select>
				<input class="configKey form-control" data-l1key="shell_user" id="in_jeetermShellUser" style="margin-top:5px;display:none;" placeholder="{{nom du compte}}" />
			</div>
			<div class="col-lg-6">
				<span class="help-block">{{www-data est le compte sous lequel tourne Jeedom. root et les autres comptes passent par sudo ; un compte inexistant, ou dont le shell est désactivé (nologin), est refusé à l'enregistrement.}}</span>
			</div>
		</div>
		<div class="form-group">
			<label class="col-lg-4 control-label">{{Session persistante (tmux)}}</label>
			<div class="col-lg-2">
				<input type="checkbox" class="configKey" data-l1key="tmux" />
			</div>
			<div class="col-lg-6">
				<span class="help-block">{{Le shell tourne dans une session tmux qui survit à la fermeture de l'onglet : on la retrouve telle quelle à la réouverture, commandes longues comprises. Toutes les fenêtres partagent alors la même session. tmux est installé avec les dépendances.}}</span>
			</div>
		</div>
		<div class="form-group">
			<label class="col-lg-4 control-label">{{Bannière d'accueil et raccourcis Jeedom}}</label>
			<div class="col-lg-2">
				<input type="checkbox" class="configKey" data-l1key="goodies" />
			</div>
			<div class="col-lg-6">
				<span class="help-block">{{À l'ouverture, une bannière résume la machine (système, version de Jeedom, mémoire, disque, adresse IP). L'invite est en couleur et montre le code d'erreur de la dernière commande, et des raccourcis s'ajoutent : jlog (suivre un journal), jcd (dossier d'un plugin), jerr, jplugins, jinfo, jrestart ; jhelp les liste. Le fichier ~/.bashrc du compte reste lu. Pour un compte dont le shell n'est pas bash, rien ne change.}}</span>
			</div>
		</div>
		<div class="form-group">
			<label class="col-lg-4 control-label">{{Fermeture après inactivité}}</label>
			<div class="col-lg-2">
				<div class="input-group">
					<input type="number" min="0" max="10080" step="1" class="configKey form-control" data-l1key="idle_timeout" />
					<span class="input-group-addon">min</span>
				</div>
			</div>
			<div class="col-lg-6">
				<span class="help-block">{{Un terminal sans aucune frappe au clavier pendant ce délai est fermé. Une commande qui affiche en continu ne compte pas comme une activité. 0 désactive la fermeture ; 10080 (une semaine) au plus.}}</span>
			</div>
		</div>
		<div class="form-group">
			<label class="col-lg-4 control-label">{{Sessions simultanées}}</label>
			<div class="col-lg-2">
				<input type="number" min="1" max="20" step="1" class="configKey form-control" data-l1key="max_sessions" />
			</div>
			<div class="col-lg-6">
				<span class="help-block">{{Nombre maximal de terminaux ouverts en même temps, tous administrateurs confondus (1 à 20).}}</span>
			</div>
		</div>
	</fieldset>

	<fieldset>
		<legend><i class="fas fa-shield-alt"></i> {{Accès}}</legend>
		<div class="form-group">
			<label class="col-lg-4 control-label">{{Réseau local uniquement}}</label>
			<div class="col-lg-2">
				<input type="checkbox" class="configKey" data-l1key="lan_only" />
			</div>
			<div class="col-lg-6">
				<span class="help-block">{{Refuse d'ouvrir un terminal depuis l'extérieur (accès distant, DNS Jeedom, proxy inverse). C'est un filtre de confort, pas une protection absolue : l'adresse du client est déduite d'en-têtes HTTP. Pris en compte immédiatement, sans redémarrage.}}</span>
			</div>
		</div>
	</fieldset>

	<fieldset>
		<legend><i class="fas fa-cogs"></i> {{Démon}}</legend>
		<div class="form-group">
			<label class="col-lg-4 control-label">{{Port local}}</label>
			<div class="col-lg-2">
				<input type="number" min="1024" max="65535" step="1" class="configKey form-control" data-l1key="port" />
			</div>
			<div class="col-lg-6">
				<span class="help-block">{{Port sur lequel le démon écoute, sur 127.0.0.1 seulement ; Apache y relaie le terminal. À changer seulement s'il est déjà pris, puis relancer l'installation des dépendances.}}</span>
			</div>
		</div>
	</fieldset>
</form>

<script>
/*
 * Le compte du shell tient dans une seule clé : vide pour www-data, « root », ou
 * un nom de compte. La liste n'est qu'une aide à la saisie, synchronisée avec le
 * champ texte qui porte la clé. Le coeur déclenche « change » sur ce champ en y
 * chargeant la valeur enregistrée : c'est là que la liste se met à jour.
 */
(function() {
	var select = document.getElementById('sel_jeetermShellUser')
	var input = document.getElementById('in_jeetermShellUser')
	if (!select || !input) {
		return
	}
	input.addEventListener('change', function() {
		var value = input.value.trim()
		if (value === '' || value === 'root') {
			select.value = value
			input.style.display = 'none'
		} else {
			select.value = 'other'
			input.style.display = ''
		}
	})
	select.addEventListener('change', function() {
		if (select.value === 'other') {
			if (input.value === '' || input.value === 'root') {
				input.value = ''
			}
			input.style.display = ''
			input.focus()
		} else {
			input.value = select.value
			input.style.display = 'none'
		}
	})
})()
</script>
