package main

import (
	"fmt"
	"os"
	"os/exec"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Println("Usage: goansible [production|staging|development] [--extra-vars='key=value'] ...")
		os.Exit(1)
	}

	env := os.Args[1]
	inventory := fmt.Sprintf("inventories/%s/hosts", env)

	// Check if inventory exists
	if _, err := os.Stat(inventory); os.IsNotExist(err) {
		fmt.Printf("❌ Inventory '%s' not found\n", inventory)
		os.Exit(2)
	}

	// Build Ansible command
	args := []string{
		"-i", inventory,
		"playbooks/deploy.yml",
	}
	if len(os.Args) > 2 {
		args = append(args, os.Args[2:]...)
	}

	cmd := exec.Command("ansible-playbook", args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	fmt.Printf("🚀 Running: ansible-playbook %v\n", args)

	// Run Ansible
	if err := cmd.Run(); err != nil {
		fmt.Printf("❌ Ansible failed: %s\n", err)
		os.Exit(3)
	}

	fmt.Println("✅ Done.")
}
